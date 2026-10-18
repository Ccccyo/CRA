from dataclasses import dataclass

from cra.ingest.chunker import Chunk


@dataclass
class Hit:
    chunk: Chunk
    score: float
