import re
from typing import List, Generator, Any


def detokenize(tokens):
    """
    Detokenizing a text undoes the tokenizing operation, restores
    punctuation and spaces to the places that people expect them to be.
    Ideally, `detokenize(tokenize(text))` should be identical to `text`,
    except for line breaks.
    """
    text = ' '.join(tokens)
    step0 = text.replace('. . .', '...')
    step1 = step0.replace("`` ", '"').replace(" ''", '"')
    step2 = step1.replace(" ( ", " (").replace(" ) ", ") ")
    step3 = re.sub(r' ([.,:;?!%]+)([ \'"`])', r"\1\2", step2)
    step4 = re.sub(r' ([.,:;?!%]+)$', r"\1", step3)
    step5 = step4.replace(" '", "'").replace(" n't", "n't") \
        .replace(" nt", "nt").replace("can not", "cannot")
    step6 = step5.replace(" ` ", " '")
    return step6.strip()


def ngramize(items: List[str], ngram_range=(1, 1)) -> List[str]:
    """
    Make ngrams from a list of tokens/lemmas
    :param items: list of tokens, lemmas or other strings to form ngrams
    :param ngram_range: range for producing ngrams, ex. for unigrams + bigrams should be set to
    (1, 2), for bigrams only should be set to (2, 2)
    :return: ngrams (as strings) generator
    """

    ngrams = []
    ranges = [(0, i) for i in range(ngram_range[0], ngram_range[1] + 1)]
    for r in ranges:
        ngrams += list(zip(*[items[j:] for j in range(*r)]))

    formatted_ngrams = [' '.join(item) for item in ngrams]

    return formatted_ngrams


def replace(items, replace_dict):
    """
    Replace a token/lemma with a replacer codename.
    Ex. usage:
        replaced = replace(['1', 'hello'], {str.isnumeric: 'NUM'})
    :param items: tokens/lemmas to replace
    :param replace_dict: a dict with String types to replace and corresponding replacers.
    Ex.: {'isnumeric': 'NUM', 'isalpha': 'WORD'}
    :return: replaced items
    """
    replaced = []
    for item in items:
        for item_type, replacer in replace_dict.items():
            if item_type(item):
                replaced.append(replacer)
                break
        else:
            replaced.append(item)
    return replaced
