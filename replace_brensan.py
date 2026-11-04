with open('ui/product_tree_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace company_stock with company_stock
content = content.replace('row["company_stock"]', 'row.get("company_stock")')

with open('ui/product_tree_window.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')