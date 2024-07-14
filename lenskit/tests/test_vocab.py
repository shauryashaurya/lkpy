"""
Tests for the Vocabulary class.
"""

from uuid import UUID

import numpy as np

import hypothesis.strategies as st
from hypothesis import assume, given
from pytest import raises

from lenskit.data import Vocabulary


@given(
    st.one_of(
        st.sets(st.integers()),
        st.sets(st.emails()),
        st.sets(st.uuids()),
    )
)
def test_create_basic(keys: set[int | str | UUID]):
    vocab = Vocabulary(keys)
    assert vocab.size == len(keys)
    assert len(vocab) == len(keys)

    index = vocab.index
    assert all(index.values == sorted(keys))


@given(
    st.one_of(
        st.lists(st.integers()),
        st.lists(st.emails()),
        st.lists(st.uuids()),
    )
)
def test_create_nonunique(keys: list[int | str | UUID]):
    uq = set(keys)
    vocab = Vocabulary(keys)
    assert vocab.size == len(uq)
    assert len(vocab) == len(uq)

    index = vocab.index
    assert all(index.values == sorted(uq))


@given(
    st.one_of(
        st.sets(st.integers()),
        st.sets(st.emails()),
        st.sets(st.uuids()),
    )
)
def test_lookup_id_index(keys: set[int | str | UUID]):
    klist = sorted(keys)

    vocab = Vocabulary(keys)
    assert vocab.size == len(klist)
    assert len(vocab) == len(klist)

    # make sure the numbers are right
    assert all([vocab.number(k) == i for (i, k) in enumerate(klist)])

    # make sure the IDs are right
    assert all([vocab.term(i) == k for (i, k) in enumerate(klist)])


@given(
    st.one_of(
        st.sets(st.integers()),
        st.sets(st.emails()),
        st.sets(st.uuids()),
    ),
    st.one_of(st.integers(), st.emails(), st.uuids()),
)
def test_lookup_bad_id(keys: set[int | str | UUID], key: int | str | UUID):
    assume(key not in keys)

    vocab = Vocabulary(keys)

    assert vocab.number(key, missing="negative") < 0

    with raises(KeyError):
        assert vocab.number(key, missing="error")


@given(
    st.one_of(
        st.sets(st.integers()),
        st.sets(st.emails()),
        st.sets(st.uuids()),
    ),
    st.one_of(st.integers()),
)
def test_lookup_bad_number(keys: set[int | str | UUID], num: int):
    assume(num < 0 or num >= len(keys))

    vocab = Vocabulary(keys)

    with raises(IndexError):
        assert vocab.term(num)


@given(
    st.sets(st.integers()),
    st.lists(st.integers()),
)
def test_lookup_many_nums(terms: set[int], lookup: list[int]):
    klist = sorted(terms)
    kpos = dict(zip(klist, range(len(klist))))

    vocab = Vocabulary(terms)

    nums = vocab.numbers(lookup, missing="negative")
    assert len(nums) == len(lookup)
    for n, k in zip(nums, lookup):
        if n < 0:
            assert k not in terms
        else:
            assert n == kpos[k]


@given(
    st.sets(st.integers()),
    st.lists(st.integers()),
)
def test_lookup_many_terms(terms: set[int], lookup: list[int]):
    lkarr = np.ndarray(lookup)
    assume(np.all(lkarr >= 0))
    assume(np.all(lkarr < len(terms)))
    klist = sorted(terms)

    vocab = Vocabulary(terms)

    keys = vocab.terms(lookup)
    assert len(keys) == len(lookup)
    for k, n in zip(keys, lookup):
        assert k == klist[n]
