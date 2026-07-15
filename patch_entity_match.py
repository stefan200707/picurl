import re

with open("app/parsing/entity_match.py", "r") as f:
    content = f.read()

# find the block and replace it
old_block = """        if has_capital or trigger_type or has_keyword:
            query_norm = normalize(window_text)
            if trigger_type or has_keyword:
                threshold = TRIGGERED_SCORE_THRESHOLD
            else:
                threshold = SCORE_THRESHOLD

            valid_choices = choices"""

new_block = """        query_norm = normalize(window_text)
        if trigger_type or has_keyword:
            threshold = TRIGGERED_SCORE_THRESHOLD
        else:
            threshold = SCORE_THRESHOLD

        valid_choices = choices"""

content = content.replace(old_block, new_block)

# also need to unindent the block inside the `if`
old_inner = """            if trigger_type:
                valid_choices = [c for c in choices if c[1] == trigger_type]

            choice_strings = [c[0] for c in valid_choices]
            if not choice_strings:
                continue

            res = process.extract(query_norm, choice_strings, scorer=fuzz.WRatio, limit=3)

            good_res = [r for r in res if r[1] >= threshold]
            if good_res:
                best_score = good_res[0][1]
                top_matches = [r for r in good_res if best_score - r[1] < 5.0]

                matched_entities_info = []
                for _matched_str, score, idx in top_matches:
                    _, etype, entry = valid_choices[idx]
                    matched_entities_info.append((score, etype, entry))

                seen = set()
                unique_entities = []
                for score, etype, entry in matched_entities_info:
                    if (etype, entry.name) not in seen:
                        seen.add((etype, entry.name))
                        adj_score = _adjust_score(score, etype, window_text, text_before, entry)
                        unique_entities.append((adj_score, score, etype, entry))

                if not unique_entities:
                    continue

                unique_entities.sort(key=lambda x: x[0], reverse=True)
                best_adj_score = unique_entities[0][0]

                # Map back to original structure for candidates
                final_unique = [
                    (orig_score, etyp, ent) for adj, orig_score, etyp, ent in unique_entities
                ]

                actual_start_idx = start_idx
                if trigger_type and trigger_start is not None:
                    actual_start_idx = trigger_start

                candidates.append(
                    {
                        "span": (actual_start_idx, end_idx),
                        "text": window_text,
                        "matches": final_unique,
                        "best_score": best_adj_score,
                        "window_size": len(window_tokens),
                    }
                )"""

new_inner = """        if trigger_type:
            valid_choices = [c for c in choices if c[1] == trigger_type]

        choice_strings = [c[0] for c in valid_choices]
        if not choice_strings:
            continue

        res = process.extract(query_norm, choice_strings, scorer=fuzz.WRatio, limit=3)

        good_res = [r for r in res if r[1] >= threshold]
        if good_res:
            best_score = good_res[0][1]
            top_matches = [r for r in good_res if best_score - r[1] < 5.0]

            matched_entities_info = []
            for _matched_str, score, idx in top_matches:
                _, etype, entry = valid_choices[idx]
                matched_entities_info.append((score, etype, entry))

            seen = set()
            unique_entities = []
            for score, etype, entry in matched_entities_info:
                if (etype, entry.name) not in seen:
                    seen.add((etype, entry.name))
                    adj_score = _adjust_score(score, etype, window_text, text_before, entry)
                    unique_entities.append((adj_score, score, etype, entry))

            if not unique_entities:
                continue

            unique_entities.sort(key=lambda x: x[0], reverse=True)
            best_adj_score = unique_entities[0][0]

            # Map back to original structure for candidates
            final_unique = [
                (orig_score, etyp, ent) for adj, orig_score, etyp, ent in unique_entities
            ]

            actual_start_idx = start_idx
            if trigger_type and trigger_start is not None:
                actual_start_idx = trigger_start

            candidates.append(
                {
                    "span": (actual_start_idx, end_idx),
                    "text": window_text,
                    "matches": final_unique,
                    "best_score": best_adj_score,
                    "window_size": len(window_tokens),
                }
            )"""

content = content.replace(old_inner, new_inner)

with open("app/parsing/entity_match.py", "w") as f:
    f.write(content)
