#!/usr/bin/env python3
"""Replace references to removed optional dispatch inputs with false."""
import re
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
text = path.read_text()
root = yaml.compose(text)
def mapping(node):
    return {key.value: value for key, value in node.value}
inputs = mapping(mapping(mapping(mapping(root)['on'])['workflow_dispatch'])['inputs'])
for key in ('use_nomount', 'test_release_notes'):
    if key not in inputs:
        text = re.sub(r'\binputs\.' + re.escape(key) + r'\b', 'false', text)
path.write_text(text)
