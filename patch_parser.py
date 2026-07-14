import re

with open('app/parsing/parser.py', 'r') as f:
    content = f.read()

# I want to replace:
old_block = """    for start, end in unconsumed_spans:
        chunk = text[start:end]
        if _is_significant(chunk):
            cleaned_chunk = chunk.strip(" ,.-:;!?")
            if cleaned_chunk:
                warnings.append(f"«{cleaned_chunk}»: не удалось распознать, не попало в ссылку")"""

new_block = """    for start, end in unconsumed_spans:
        chunk = text[start:end]
        # Разбиваем нераспознанный текст по знакам препинания и союзам, чтобы давать более точные предупреждения
        subchunks = re.split(r'[,;.]|\s+и\s+|\s+а\s+|\s+но\s+', chunk)
        for subchunk in subchunks:
            if _is_significant(subchunk):
                cleaned_chunk = subchunk.strip(" ,.-:;!?")
                if cleaned_chunk:
                    warnings.append(f"«{cleaned_chunk}»: не удалось распознать, не попало в ссылку")"""

content = content.replace(old_block, new_block)

with open('app/parsing/parser.py', 'w') as f:
    f.write(content)

