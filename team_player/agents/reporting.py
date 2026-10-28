"""Reporting-only unit labels. Never transforms a numeric value or scoring formula."""
import re

_NUMBER = r'([+−-]?\d+(?:\.\d+)?)'

def report_text(text):
    # Restrict replacements to named derived scores. Shot/counter xG stays xG.
    text = text.replace('His own value: position vs an average player in his role, minus the cost he caused (expected goals)',
                        'Estimated net contribution (model units)')
    text = text.replace('Team outcome while he did it, shared with team-mates (expected goals)',
                        'Shared team value change (model units)')
    for label in ('own value', 'team outcome', 'defending value', 'estimated net contribution'):
        text = re.sub(r'('+label+r'\s*:?\s*)'+_NUMBER+r'\s+(?:xG|expected goals)\b',
                      r'\1\2 model units', text, flags=re.I)
    text = re.sub(r"(own movement was worth\s+)"+_NUMBER+r' expected goals\b',r'\1\2 model units',text,flags=re.I)
    text = re.sub(r'xG prevented(?=\s*:?\s*[+−-]?\d)', 'shared defensive value change', text, flags=re.I)
    text = re.sub(r'(shared defensive value change\s*:?\s*)'+_NUMBER+r'(?![\d.])(?:\s+(?:xG|expected goals))?(?=\s+per)',
                  r'\1\2 model units',text,flags=re.I)
    return text

def report_units(value):
    if isinstance(value,str):return report_text(value)
    if isinstance(value,dict):return {k:report_units(v) for k,v in value.items()}
    if isinstance(value,list):return [report_units(v) for v in value]
    if isinstance(value,tuple):return tuple(report_units(v) for v in value)
    return value


INVALID_COMPARISON = "Please select players in the same position in different teams"
def valid_comparison(left, right):
    return bool(left and right and left[0] != right[0] and left[1:] == right[1:])
