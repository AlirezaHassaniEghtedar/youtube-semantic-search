import re
from langchain_text_splitters import RecursiveCharacterTextSplitter

def preprocess_english_transcript(raw_text):
    if not raw_text:
        return ""

    # cleaning the text from interjections and hesitations
    fillers = [
        r'\bum+\b',
        r'\bmm+h?\b',
        r'\buh+\b',
        r'\bah+\b',
        r'\ber+\b',
        r'\beh+\b',
        r'\bhm+\b',
        r'\boh+\b',
        r'\boops+\b',
        r'\bsh+\b',
        r'\byou know\b',
        r'\bi mean\b',
    ]

    combined_pattern = '|'.join(fillers)
    cleaned_text = re.sub(combined_pattern, '', raw_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(r'\b(\w+)(?:\s+\1\b)+', r'\1', cleaned_text, flags=re.IGNORECASE)  #duplicate words
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()

    return cleaned_text


def chunk(segments_data: list[dict], chunk_size: int = 500, chunk_overlap: int = 100) -> list[dict]:
    full_text = ""
    time_mapping = []

    for seg in segments_data:
        clean_text = preprocess_english_transcript(seg["text"])
        if not clean_text:
            continue

        start_char_idx = len(full_text)
        full_text += clean_text + " "
        end_char_idx = len(full_text) - 1

        time_mapping.append({
            "char_start": start_char_idx,
            "char_end": end_char_idx,
            "time_start": seg["start"],
            "time_end": seg["end"]
        })

    if not full_text:
        return []

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=['\n\n', '\n', ' ', '']
    )

    raw_chunks = text_splitter.split_text(full_text)

    final_segments = []
    search_start_idx = 0

    for chunk in raw_chunks:
        chunk_idx = full_text.find(chunk, search_start_idx)
        if chunk_idx == -1:
            chunk_idx = search_start_idx

        chunk_end_idx = chunk_idx + len(chunk)
        search_start_idx = chunk_idx + 1

        chunk_time_start = None
        chunk_time_end = None

        for m in time_mapping:
            if m["char_end"] > chunk_idx and m["char_start"] < chunk_end_idx:
                if chunk_time_start is None:
                    chunk_time_start = m["time_start"]
                chunk_time_end = m["time_end"]

        if chunk_time_start is not None:
            final_segments.append({
                "start": chunk_time_start,
                "end": chunk_time_end,
                "text": chunk
            })

    return final_segments