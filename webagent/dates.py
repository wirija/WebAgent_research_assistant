"""Conservative dates from a document's own row, never from a filename/year."""
import re
from dateutil.parser import parse

MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE = rf"\b(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}\s+{MONTH}\s+\d{{4}}|{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+\d{{4}}|\d{{1,2}}[/.]\d{{1,2}}[/.]\d{{4}})\b"


def explicit_dates(text):
    found = []
    for match in re.finditer(DATE, text, re.I):
        raw = match.group()
        try:
            if re.fullmatch(r"\d{1,2}[/.]\d{1,2}[/.]\d{4}", raw):
                value = parse(raw, dayfirst=True).date()
                if value != parse(raw, dayfirst=False).date():
                    continue
            else:
                value = parse(raw).date()
            found.append((value, raw))
        except (ValueError, OverflowError):
            pass
    return found


def link_date(link):
    # A separate date cell/time element is preferable to flattened row text.
    context = link.get("date_context", "")
    found = explicit_dates(context)
    if len({d for d, _ in found}) == 1:
        return found[0]
    if context:
        return None, ""
    # Common archive layout: <li><a>Title (possibly with a period date)</a>
    # Publication date</li>. Remove the exact title, then require that ONLY a
    # date and an optional download label remain. Never borrow a nearby row.
    context = re.sub(r"\s+", " ", link.get("context", "")).strip()
    label = re.sub(r"\s+", " ", link.get("label", "")).strip()
    if label and context.count(label) == 1:
        outside = context.replace(label, "", 1).strip(" |–—:-")
        if re.fullmatch(rf"{DATE}(?:\s+(?:PDF|Download|View PDF))?", outside, re.I):
            dates = explicit_dates(outside)
            if len(dates) == 1:
                return dates[0]
    # Support saved observations and list layouts: only an explicit date followed
    # by a file/download label. A report-period date at the end of a title alone
    # is deliberately insufficient.
    context = link.get("context", "")
    match = re.search(rf"({DATE})\s+(?:PDF|Download|View PDF)\s*$", context, re.I)
    if match:
        if match.group(1) in link.get("label", "") or re.search(r"(?:ended|ending|as at|as of|period to|through|to)\s*$", context[:match.start()], re.I):
            return None, ""
        found = explicit_dates(match.group(1))
        if len(found) == 1:
            return found[0]
    return None, ""
