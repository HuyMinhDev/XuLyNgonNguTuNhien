"""BM25 for Vietnamese: syllables plus adjacent-syllable bigrams.

Vietnamese writes multi-syllable words with spaces ("nước dưới đất", "khai thác"), so the
ViHERMES syllable tokens lose word boundaries. Adding syllable bigrams restores most compound
words without a word segmenter. Scoring is the ViHERMES BM25 formula unchanged.
"""

from collections import Counter, defaultdict

import numpy as np

from ..retrieval import BM25, tokens


def terms(text):
    syllables = tokens(text)
    return syllables + [f"{a}_{b}" for a, b in zip(syllables, syllables[1:])]


class BigramBM25(BM25):
    def __init__(self, nodes, k1=1.5, b=0.75):
        self.ids = [n.id for n in nodes]
        self.k1, self.b = k1, b
        bags = [Counter(terms(n.text)) for n in nodes]
        self.lengths = np.array([sum(bag.values()) for bag in bags], dtype=float)
        self.avg = max(float(self.lengths.mean()) if nodes else 0, 1)
        self.postings = defaultdict(list)
        for i, bag in enumerate(bags):
            for term, count in bag.items():
                self.postings[term].append((i, count))

    def scores(self, query):
        # The parent scorer tokenizes into syllables; score the expanded terms instead.
        result = np.zeros(len(self.ids))
        for term in set(terms(query)):
            postings = self.postings.get(term, [])
            idf = np.log(1 + (len(self.ids) - len(postings) + 0.5) / (len(postings) + 0.5))
            for i, count in postings:
                result[i] += (
                    idf
                    * count
                    * (self.k1 + 1)
                    / (count + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg))
                )
        return result
