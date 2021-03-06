from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import concurrent
from typing import List, Any, Generator, Tuple, KeysView, ValuesView, Dict

import scipy as sp
from scipy import sparse
import numpy as np

from tokenizers.spacy_tokenizer import SpacyTokenizer
from logger import logger
from utils import hash

TOKENIZER = None


class HashingTfIdfVectorizer:
    """
    Create a tfidf matrix from collection of documents.
    """

    def __init__(self, data_iterator, hash_size=2 ** 24, tokenizer=SpacyTokenizer(ngram_range=(1,2))):
        """

        :param data_iterator: an instance of an iterator class, producing data batches;
        the iterator class should implement read_batch() method
        :param hash_size: a size of hash, power of 2
        :param tokenizer: an instance of a tokenizer class; should implement "lemmatize()"
         and/or "tokenize() methods"
        """
        self.doc_index = data_iterator.doc2index
        # self.doc2index = None
        self.hash_size = hash_size
        self.tokenizer = tokenizer

        global TOKENIZER
        TOKENIZER = self.tokenizer

        if hasattr(self.tokenizer, 'lemmatize'):
            processing_fn = self.tokenizer.lemmatize
        elif hasattr(self.tokenizer, 'tokenize'):
            processing_fn = self.tokenizer.tokenize
        else:
            raise AttributeError("{} should implement either 'tokenize()' or lemmatize()".
                                 format(self.tokenizer.__class__.__name__))

        self.processing_fn = processing_fn
        self.data_iterator = data_iterator

        self.tfidf_matrix = None
        self.term_freqs = None

    def get_counts(self, docs: List[str], doc_ids: List[Any]) \
            -> Generator[Tuple[KeysView, ValuesView, List[int]], Any, None]:
        logger.info("Tokenizing batch...")
        batch_ngrams = list(self.processing_fn(docs))
        logger.info("Counting hash...")
        doc_id = iter(doc_ids)
        for ngrams in batch_ngrams:
            counts = Counter([hash(gram, self.hash_size) for gram in ngrams])
            hashes = counts.keys()
            values = counts.values()
            _id = self.doc_index[next(doc_id)]
            if values:
                col_id = [_id] * len(values)
            else:
                col_id = []
            yield hashes, values, col_id

    @staticmethod
    def get_counts_parallel(kwargs) -> Tuple[List[int], List[int], List[int]]:
        """
        Get batch counts. The same as get_counts(), but rewritten as staticmethod to be suitable
        for parallelization.
        """

        docs = kwargs['docs']
        doc_ids = kwargs['doc_ids']
        index = kwargs['doc_index']
        hash_size = kwargs['hash_size']

        logger.info("Tokenizing batch...")

        if hasattr(TOKENIZER, 'lemmatize'):
            processing_fn = TOKENIZER.lemmatize
        elif hasattr(TOKENIZER, 'tokenize'):
            processing_fn = TOKENIZER.tokenize
        else:
            raise AttributeError("{} should implement either 'tokenize()' or lemmatize()".
                                 format(TOKENIZER.__class__.__name__))

        batch_ngrams = list(processing_fn(docs))
        doc_id = iter(doc_ids)

        batch_hashes = []
        batch_values = []
        batch_col_ids = []

        logger.info("Counting hash...")
        for ngrams in batch_ngrams:
            counts = Counter([hash(gram, hash_size) for gram in ngrams])
            hashes = counts.keys()
            values = counts.values()
            col_id = [index[next(doc_id)]] * len(values)
            batch_hashes.extend(hashes)
            batch_values.extend(values)
            batch_col_ids.extend(col_id)

        return batch_hashes, batch_values, batch_col_ids

    def get_count_matrix(self, row: List[int], col: List[int], data: List[int], size) \
            -> sp.sparse.csr_matrix:
        count_matrix = sparse.csr_matrix((data, (row, col)), shape=(self.hash_size, size))
        count_matrix.sum_duplicates()
        return count_matrix

    @staticmethod
    def get_tfidf_matrix(count_matrix: sp.sparse.csr_matrix) -> Tuple[sp.sparse.csr_matrix, np.array]:
        """Convert a word count matrix into a tfidf matrix."""

        binary = (count_matrix > 0).astype(int)
        term_freqs = np.array(binary.sum(1)).squeeze()
        idfs = np.log((count_matrix.shape[1] - term_freqs + 0.5) / (term_freqs + 0.5))
        idfs[idfs < 0] = 0
        idfs = sp.sparse.diags(idfs, 0)
        tfs = count_matrix.log1p()
        tfidfs = idfs.dot(tfs)
        return tfidfs, term_freqs

    def fit(self) -> None:
        rows = []
        cols = []
        data = []

        for docs, doc_ids in self.data_iterator.read_batch():
            for batch_rows, batch_data, batch_cols in self.get_counts(docs, doc_ids):
                rows.extend(batch_rows)
                cols.extend(batch_cols)
                data.extend(batch_data)

        count_matrix = self.get_count_matrix(rows, cols, data, size=len(self.doc_index))
        tfidf_matrix, term_freqs = self.get_tfidf_matrix(count_matrix)
        self.term_freqs = term_freqs
        self.tfidf_matrix = tfidf_matrix

    def fit_parallel(self, n_jobs=1) -> None:

        rows = []
        cols = []
        data = []

        with ProcessPoolExecutor(max_workers=n_jobs) as executor:
            futures = [executor.submit(self.get_counts_parallel, (
                {'docs': docs, 'doc_ids': doc_ids, 'doc_index': self.doc_index,
                 'hash_size': self.hash_size})) for docs, doc_ids
                       in
                       self.data_iterator.read_batch()]

            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                rows.extend(result[0])
                data.extend(result[1])
                cols.extend(result[2])

        count_matrix = self.get_count_matrix(rows, cols, data, size=len(self.doc_index))
        tfidf_matrix, term_freqs = self.get_tfidf_matrix(count_matrix)
        self.term_freqs = term_freqs
        self.tfidf_matrix = tfidf_matrix

    def transform(self, docs: List[str]) -> sp.sparse.csr_matrix:

        docs_tfidfs = []

        # TODO Try to vectorize w/o for loop

        for doc in docs:
            ngrams = list(self.processing_fn([doc]))
            hashes = [hash(ngram, self.hash_size) for ngram in ngrams[0]]

            hashes_unique, q_hashes = np.unique(hashes, return_counts=True)
            tfs = np.log1p(q_hashes)

            # TODO ? revise policy if len(q_hashes) == 0

            if len(q_hashes) == 0:
                return sp.sparse.csr_matrix((1, self.hash_size))

            size = len(self.doc_index)
            Ns = self.term_freqs[hashes_unique]
            idfs = np.log((size - Ns + 0.5) / (Ns + 0.5))
            idfs[idfs < 0] = 0

            tfidf = np.multiply(tfs, idfs)

            indptr = np.array([0, len(hashes_unique)])
            sp_tfidf = sp.sparse.csr_matrix(
                (tfidf, hashes_unique, indptr), shape=(1, self.hash_size)
            )
            docs_tfidfs.append(sp_tfidf)

        transformed = sp.sparse.vstack(docs_tfidfs)

        return transformed

    def save(self, save_path: str) -> None:

        logger.info('Saving tfidf model to {}'.format(save_path))

        opts = {'hash_size': self.hash_size,
                'ngram_range': self.tokenizer.ngram_range,
                'doc_index': self.doc_index,
                'term_freqs': self.term_freqs}

        data = {
            'data': self.tfidf_matrix.data,
            'indices': self.tfidf_matrix.indices,
            'indptr': self.tfidf_matrix.indptr,
            'shape': self.tfidf_matrix.shape,
            'opts': opts
        }
        np.savez(save_path, **data)

    def load(self, load_path: str) -> None:
        logger.info('Loading tfidf model from {}'.format(load_path))
        loader = np.load(load_path)

        self.tfidf_matrix = sp.sparse.csr_matrix((loader['data'], loader['indices'],
                                                  loader['indptr']), shape=loader['shape'])
        opts = loader['opts'].item(0)

        ngram_range = opts['ngram_range']

        assert self.tokenizer.ngram_range == ngram_range, \
            'The tokenizer attribute of HashingTdidfVectorizer' \
            'was initialized with different ngram_range than the loaded model'

        self.hash_size = opts['hash_size']
        self.term_freqs = opts['term_freqs'].squeeze()
        self.doc_index = opts['doc_index']

# PATH='/media/olga/Data/projects/iPavlov/DeepPavlov/download/odqa/ruwiki_tfidf_matrix.npz'
# vectorizer = HashingTfIdfVectorizer(None)
# vectorizer.load(PATH)
# vectorizer.save('/media/olga/Data/projects/iPavlov/DeepPavlov/download/odqa/ruwiki_tfidf_matrix_new.npz')

