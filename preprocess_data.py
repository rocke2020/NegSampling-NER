import re, random
from pathlib import Path
from typing import List
import pickle
import json
from pprint import pprint
import os, sys

root = '/data2/corpus/nlp_corpus/conll2003'
out_path = 'dataset'


def convert_data():
    for file in Path(root).glob('*.txt'):
        with open(file, 'r', encoding='utf-8') as f:
            sentences = []
            words = []
            labels = []
            start_index = 0
            lines = f.readlines()
            length = len(lines)
            left_index, right_index = 0, 0
            for line_index, line in enumerate(lines):
                if line_index < 2: continue
                if line.strip():
                    items = line.split()
                    assert len(items) == 4
                    word = items[0]
                    label = items[-1]
                    words.append(word)
                    if '-' in label:
                        location, entity = label.split('-')
                        if location == 'B':
                            left_index = start_index
                        has_next = False
                        if line_index + 1 < length:
                            next_line = lines[line_index+1]
                            if next_line.strip():
                                items = next_line.split()
                                assert len(items) == 4
                                label = items[-1]
                                if '-' in label:
                                    location, entity = label.split('-')
                                    if location == 'I':
                                        has_next = True
                        if not has_next:
                            right_index = start_index
                            labels.append((left_index, right_index, entity))
                    start_index += 1
                # a new sentence
                else:
                    result = {
                        'sentence': words.copy(),
                        "labeled entities": labels.copy()
                    }
                    sentences.append(result)
                    words.clear()
                    labels.clear()
                    start_index = 0
                    left_index, right_index = 0, 0
            json_file = Path(out_path, file.stem+'.json')
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(sentences, f)


convert_data()
