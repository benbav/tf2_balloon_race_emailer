import re

# Servers that run Balloon Race all the time; everything else only rotates onto the map now and then.
# A server counts as dedicated if its name matches, or its address is listed here.
DEDICATED_NAME_RE = re.compile(r'balloon|bloon|balloon race 24/7', re.I)

DEDICATED_ADDRS = set()


# Some servers pad their names with control characters, which browsers and plotly.js can't render
def clean_name(name):
    return re.sub(r'[\x00-\x1f\x7f]', '', name).strip()


def is_dedicated(name, addr=None):
    return addr in DEDICATED_ADDRS or bool(DEDICATED_NAME_RE.search(name))
