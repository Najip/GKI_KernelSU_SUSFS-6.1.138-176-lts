#!/usr/bin/env python3
"""Safely generate a candidate using the existing fork customization rules."""
import runpy
import sys
from pathlib import Path
import yaml


def mapping(node):
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("Expected YAML mapping")
    result = {}
    for key, value in node.value:
        if key.value in result:
            raise ValueError(f"Duplicate key: {key.value}")
        result[key.value] = value
    return result


def inputs_node(text):
    root = mapping(yaml.compose(text))
    return mapping(mapping(root['on'])['workflow_dispatch'])['inputs']


def count_inputs(text):
    return len(mapping(inputs_node(text)))


def remove_input(lines, key):
    text = '\n'.join(lines)
    pairs = inputs_node(text).value
    for i, (name, value) in enumerate(pairs):
        if name.value == key:
            start = name.start_mark.line
            end = pairs[i + 1][0].start_mark.line if i + 1 < len(pairs) else value.end_mark.line
            return lines[:start] + lines[end:]
    return lines


def complete_cfs_input(text):
    for name, value in inputs_node(text).value:
        if name.value != 'use_cfs_bw':
            continue
        props = mapping(value)
        additions = []
        indent = ' ' * (name.start_mark.column + 2)
        if 'type' not in props:
            additions.append(indent + 'type: boolean')
        if 'default' not in props:
            additions.append(indent + 'default: false')
        if additions:
            lines = text.split('\n')
            index = name.start_mark.line + 1
            lines[index:index] = additions
            return '\n'.join(lines)
    return text


def validate(text):
    root = mapping(yaml.compose(text))
    jobs = mapping(root['jobs'])
    resolve = mapping(jobs['resolve-sources'])
    mapping(resolve['outputs'])
    inputs = mapping(inputs_node(text))
    if len(inputs) > 25:
        raise ValueError(f"Still {len(inputs)} inputs after removing DS/preview; refusing to publish")
    for name, node in inputs.items():
        props = mapping(node)
        if 'type' not in props or 'description' not in props:
            raise ValueError(f"Incomplete input definition: {name}")
    return root


def main(src, dst):
    rules = runpy.run_path(str(Path(__file__).with_name('apply_custom_main.py')))
    fn = rules['main']
    fn.__globals__['count_inputs'] = count_inputs
    fn.__globals__['remove_input'] = remove_input
    fn(src, dst)
    text = complete_cfs_input(Path(dst).read_text())
    validate(text)
    Path(dst).write_text(text)


if __name__ == '__main__':
    main(*sys.argv[1:])
