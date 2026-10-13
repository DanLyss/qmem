"""Readers: one per record source. Each turns the source's files into the
builder's model; a source that was not recorded has no reader output.
layout.open_record() hides the difference between record v1 (flat) and
v2 (a subdirectory per source, record.json)."""
