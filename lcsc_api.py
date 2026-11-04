"""Company adapter for KiCad LCSC Manager's JSON API; no HTML scraping."""
import re
import threading
from digikey_api import add_result
from lcsc_settings import load_settings, get_usage_manager
from third_party.lcsc_manager.api.lcsc_api import LCSCAPIClient, LCSCAPIError, LCSCRateLimitError

_USAGE_LOCK = threading.Lock()


def valid_lcsc_id(value):
    value = str(value or '').strip().upper()
    if not re.fullmatch(r'C[1-9][0-9]*', value):
        raise LCSCAPIError('Select a valid LCSC part number, such as C25804.')
    return value


class LcscAPI(LCSCAPIClient):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.config = {'api_timeout': self.settings['timeout']}
        self.raw_components = {}
        self.detail_warnings = []

    def _make_request(self, method, url, params=None, json_data=None, timeout=None,
                      retry_count=0, max_retries=0):
        with _USAGE_LOCK:
            manager = get_usage_manager()
            manager.refresh_day()
            limit = self.settings['daily_limit']
            if limit and int(manager.usage.get('lcsc', 0)) >= limit:
                raise LCSCRateLimitError('The configured LCSC daily request limit has been reached.')
            manager.increment_usage('lcsc')
        # Retain upstream host pacing/TLS/errors, but leave retries to the user.
        response = super()._make_request(method, url, params, json_data, timeout,
                                         retry_count=0, max_retries=0)
        if not isinstance(response, dict):
            raise LCSCAPIError('The supplier returned an invalid JSON response.')
        if url == self.JLCPCB_SEARCH_URL:
            if response.get('code') != 200:
                raise LCSCAPIError('JLCPCB rejected the search request. Please try again later.')
            data = response.get('data') or {}
            for item in (data.get('componentPageInfo') or {}).get('list') or []:
                code = item.get('componentCode') or str(item.get('urlSuffix') or '').split('/')[-1]
                if re.fullmatch(r'C[1-9][0-9]*', str(code)):
                    self.raw_components[code] = item
        return response

    def _get_jlcpcb_info(self, lcsc_id, swallow_errors=True):
        try:
            return super()._get_jlcpcb_info(lcsc_id, swallow_errors=False)
        except LCSCAPIError as exc:
            if not swallow_errors:
                raise
            self.detail_warnings.append(str(exc))
            return None

    def search(self, query, max_results=20, page=1, package=''):
        query = str(query or '').strip()
        if not query:
            return []
        direct = bool(re.fullmatch(r'[cC][1-9][0-9]*', query))
        if direct:
            query = query.upper()
        rows = self.search_jlcpcb(' '.join(filter(None, (query, package.strip()))),
                                  page=page, page_size=max_results)
        products = [self._normalize(row['lcsc']['number'], {}) for row in rows
                    if re.fullmatch(r'C[1-9][0-9]*', row.get('lcsc', {}).get('number', ''))]
        if direct:
            products = [p for p in products if p['lcsc_id'] == query]
            if not products and page == 1:
                detail = self.details(query)
                return [detail] if detail else []
        return products

    def details(self, lcsc_id):
        lcsc_id = valid_lcsc_id(lcsc_id)
        self.detail_warnings = []
        component = super().get_component_complete(lcsc_id)
        if component and valid_lcsc_id(component.get('lcsc_id')) != lcsc_id:
            raise LCSCAPIError('EasyEDA returned a different LCSC part. Nothing was imported.')
        if not component and lcsc_id not in self.raw_components:
            self.get_jlcpcb_info(lcsc_id, swallow_errors=False)
        if not component and lcsc_id not in self.raw_components:
            return None
        return self._normalize(lcsc_id, component or {})

    def _normalize(self, code, component):
        raw = self.raw_components.get(code, {})
        product = dict(component)
        product.update({
            'lcsc_id': code,
            'manufacturer_part': raw.get('componentModelEn') or component.get('manufacturer_part') or '',
            'name': raw.get('componentModelEn') or component.get('name') or '',
            'manufacturer': raw.get('componentBrandEn') or component.get('manufacturer') or '',
            'description': raw.get('describe') or component.get('description') or '',
            'package': component.get('package') or raw.get('componentSpecificationEn') or '',
            'stock': raw.get('stockCount'),
            'price': [{'qty': tier.get('startNumber', 1), 'price': tier.get('productPrice')}
                      for tier in raw.get('componentPrices') or []],
            'datasheet': raw.get('dataManualUrl') or component.get('datasheet') or '',
            'url': raw.get('lcscGoodsUrl') or component.get('url') or f'https://www.lcsc.com/product-detail/{code}.html',
            'category': raw.get('componentTypeEn') or component.get('category') or '',
            'library_type': {'base': 'Basic', 'expand': 'Extended'}.get(raw.get('componentLibraryType'), ''),
            'parameters': {item['attribute_name_en']: item.get('attribute_value_name', '')
                           for item in raw.get('attributes') or [] if item.get('attribute_name_en')},
            'warnings': list(self.detail_warnings),
        })
        if raw.get('rohsFlag') in (0, 1):
            product['parameters']['RoHS'] = 'Yes' if raw['rohsFlag'] else 'No'
        # Stock availability is not a manufacturer's lifecycle status.
        product['product_status'] = raw.get('productStatus') or ''
        return product


def get_lcsc_client():
    return LcscAPI()


def get_lcsc_part_data(part_number, **unused):
    """Noninteractive callers require one exact MPN, never the first similar result."""
    client = get_lcsc_client()
    query = str(part_number or '').strip()
    if re.fullmatch(r'[cC][1-9][0-9]*', query):
        return client.details(query.upper())
    matches = [p for p in client.search(query)
               if p['manufacturer_part'].casefold() == query.casefold()]
    return client.details(matches[0]['lcsc_id']) if len(matches) == 1 else None


def extract_lcsc_fields(product):
    if not isinstance(product, dict):
        return {}
    result = {}
    para = ((product.get('easyeda_data') or {}).get('dataStr') or {}).get('head', {}).get('c_para', {})
    for key, value in {**para, **product.get('parameters', {})}.items():
        if isinstance(value, (str, int, float)):
            add_result(result, key, str(value))
    for key, value in {
        'Manufacturer Part Number': product.get('manufacturer_part'),
        'Manufacturer': product.get('manufacturer'), 'Description': product.get('description'),
        'Package': product.get('package'), 'Datasheet': product.get('datasheet'),
        'LCSC Part Number': product.get('lcsc_id'), 'URL': product.get('url'),
        'Price': get_lcsc_price(product), 'Stock': get_lcsc_stock(product),
        'Product Status': product.get('product_status'),
    }.items():
        if value is not None and value != '':
            add_result(result, key, str(value))
    for target, aliases in {'Power': ('Power(Watts)',),
                             'Voltage': ('Voltage-Supply(Max)', 'Rated Voltage'),
                             'Mounting Type': ('Mounting Style',)}.items():
        for alias in aliases:
            value = product.get('parameters', {}).get(alias)
            if value:
                add_result(result, target, str(value))
                break
    return result


def get_lcsc_price(product):
    tiers = [p for p in (product or {}).get('price', []) if p.get('price') is not None]
    return str(min(tiers, key=lambda p: p.get('qty', 1))['price']) if tiers else ''


def get_lcsc_stock(product):
    value = (product or {}).get('stock')
    return '' if value is None else str(value)


def get_lcsc_part_status(product, extracted=None):
    return (product or {}).get('product_status') or ''
