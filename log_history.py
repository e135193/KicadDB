"""Read all retained rotating log files, preserving multiline exceptions."""
from pathlib import Path
import re

HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[,.]\d+)?)\s+(\w+)\s+([^:]+):\s?(.*)$")


def read_log_history(path):
    path = Path(path)
    archives = [p for p in path.parent.glob(path.name + ".*")
                if p.name[len(path.name) + 1:].isdigit() and p.is_file()]
    archives.sort(key=lambda p: int(p.name[len(path.name) + 1:]), reverse=True)
    files = archives + ([path] if path.is_file() else [])
    rows = []
    for file in files:
        current = None
        with file.open(encoding="utf-8-sig", errors="replace") as stream:
            for number, line in enumerate(stream, 1):
                line = line.rstrip("\r\n")
                match = HEADER.match(line)
                if match:
                    stamp, level, logger, message = match.groups()
                    # Current (English) keys written by db.write_change_log, plus the
                    # Turkish keys of log files written by earlier versions.
                    fields = dict(re.findall(
                        r"(?:^|\s)(user|table|record|part|kullanici|tablo|kayit|parca)=([^\s]+)", message))
                    current = {"Changed_At": stamp,
                               "Changed_By": fields.get("user", fields.get("kullanici", "")),
                               "Action": message.split()[0] if logger == "company.audit" and message else level,
                               "Table_Name": fields.get("table", fields.get("tablo", "")),
                               "Record_Key": fields.get("record", fields.get("kayit", "")),
                               "Part_Number": fields.get("part", fields.get("parca", "")),
                               "Source": file.name, "Summary": message,
                               "Details": f"{file.name}:{number}\n{line}"}
                    rows.append(current)
                elif current is not None:
                    current["Details"] += "\n" + line
                elif line:
                    current = {"Changed_At": "", "Action": 'No date', "Source": file.name,
                               "Details": f"{file.name}:{number}\n{line}"}
                    rows.append(current)
    return sorted(reversed(rows), key=lambda row: row["Changed_At"], reverse=True)


def filter_history(rows, day='All days', text="", table=None, record_key=None):
    tokens = [token.strip().casefold() for token in text.split("+") if token.strip()]
    return [row for row in rows
            if (day == 'All days' or (day == 'No date' and not row.get("Changed_At"))
                or row.get("Changed_At", "")[:10] == day)
            and (not table or row.get("Table_Name") == table)
            and (record_key is None or str(row.get("Record_Key")) == str(record_key))
            and all(token in " ".join(str(v) for v in row.values()).casefold() for token in tokens)]
