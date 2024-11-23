# This file is part of LensKit.
# Copyright (C) 2018-2023 Boise State University
# Copyright (C) 2023-2024 Drexel University
# Licensed under the MIT license, see LICENSE.md for details.
# SPDX-License-Identifier: MIT

import logging
import pickle

import numpy as np
import pandas as pd
import torch

from pytest import approx, mark

import lenskit.util.test as lktu
from lenskit.als import ImplicitMF
from lenskit.data import Dataset, ItemList, RecQuery, from_interactions_df, load_movielens_df
from lenskit.metrics import quick_measure_model
from lenskit.pipeline import topn_pipeline

_log = logging.getLogger(__name__)

simple_df = pd.DataFrame({"item": [1, 1, 2, 3], "user": [10, 12, 10, 13]})
simple_ds = from_interactions_df(simple_df)

simple_dfr = simple_df.assign(rating=[4.0, 3.0, 5.0, 2.0])
simple_dsr = from_interactions_df(simple_dfr)


def test_als_basic_build():
    algo = ImplicitMF(20, epochs=10)
    algo.train(simple_ds)

    assert algo.users_ is not None
    assert algo.user_features_ is not None

    assert set(algo.users_.ids()) == set([10, 12, 13])
    assert set(algo.items_.ids()) == set([1, 2, 3])
    assert algo.user_features_.shape == (3, 20)
    assert algo.item_features_.shape == (3, 20)


def test_als_predict_basic():
    algo = ImplicitMF(20, epochs=10)
    algo.train(simple_ds)

    preds = algo(query=10, items=ItemList([3]))

    assert len(preds) == 1
    preds = preds.scores("pandas", index="ids")
    assert preds is not None
    assert preds.index[0] == 3
    assert preds.loc[3] >= -0.1
    assert preds.loc[3] <= 5


def test_als_predict_basic_for_new_ratings():
    """Test ImplicitMF ability to support new ratings"""
    algo = ImplicitMF(20, epochs=10)
    algo.train(simple_ds)

    query = RecQuery(15, ItemList([1, 2]))
    preds = algo(query, ItemList([3]))

    assert len(preds) == 1
    preds = preds.scores("pandas", index="ids")
    assert preds is not None
    assert preds.index[0] == 3
    assert preds.loc[3] >= -0.1
    assert preds.loc[3] <= 5


def test_als_predict_basic_for_new_user_with_new_ratings():
    """
    Test if ImplicitMF predictions using the same ratings for a new user
    is the same as a user in the current simple_df dataset.
    """
    u = 10
    i = 3

    algo = ImplicitMF(20, epochs=10, use_ratings=True)
    algo.train(simple_dsr)

    preds = algo(u, ItemList([i]))
    preds = preds.scores("pandas", index="ids")
    assert preds is not None

    query = RecQuery(1, ItemList([1, 2], rating=[4.0, 5.0]))
    new_preds = algo(query, ItemList([i]))
    new_preds = new_preds.scores("pandas", index="ids")
    assert new_preds is not None
    assert abs(preds.loc[i] - new_preds.loc[i]) <= 0.1


def test_als_predict_for_new_users_with_new_ratings(rng: np.random.Generator, ml_ds: Dataset):
    """
    Test if ImplicitMF predictions using the same ratings for a new user
    is the same as a user in ml-latest-small dataset.
    The test is run for more than one user.
    """
    n_users = 3
    n_items = 2
    new_u_id = -1

    np.random.seed(45)
    users = rng.choice(ml_ds.users.ids(), n_users)
    items = ItemList(rng.choice(ml_ds.items.ids(), n_items))

    algo = ImplicitMF(20, epochs=10, use_ratings=False)
    algo.train(ml_ds)
    assert algo.users_ is not None
    assert algo.user_features_ is not None

    _log.debug("Items: " + str(items))

    for u in users:
        _log.debug(f"user: {u}")
        preds = algo(u, items)
        preds = preds.scores("pandas", index="ids")
        assert preds is not None
        upos = algo.users_.number(u)

        # get the user's rating series
        user_data = ml_ds.user_row(u)
        assert user_data is not None

        nr_info = user_data.to_df()
        ifs = algo.item_features_[user_data.numbers(vocabulary=algo.items_), :]
        fit_uv = algo.user_features_[upos, :]
        nr_info["fit_recon"] = ifs @ fit_uv
        nr_info["fit_sqerr"] = np.square(algo.weight + 1.0 - nr_info["fit_recon"])

        _log.debug("user_features from fit:\n%s", fit_uv)
        new_uv, _new_off = algo.new_user_embedding(new_u_id, user_data)
        nr_info["new_recon"] = ifs @ new_uv
        nr_info["new_sqerr"] = np.square(algo.weight + 1.0 - nr_info["new_recon"])

        _log.debug("user features from new:\n%s", new_uv)

        _log.debug("training data reconstruction:\n%s", nr_info)

        query = RecQuery(-1, user_data)
        new_preds = algo(query, items)
        new_preds = new_preds.scores("pandas", index="ids")
        assert new_preds is not None

        _log.debug("preds: " + str(preds.values))
        _log.debug("new_preds: " + str(new_preds.values))
        _log.debug("------------")

        diffs = np.abs(preds - new_preds)
        assert all(diffs <= 0.1)


def test_als_recs_topn_for_new_users_with_new_ratings(
    rng, ml_ratings: pd.DataFrame, ml_ds: Dataset
):
    """
    Test if ImplicitMF topn recommendations using the same ratings for a new user
    is the same as a user in ml-latest-small dataset.
    The test is run for more than one user.
    """
    import scipy.stats as stats

    from lenskit.algorithms import basic

    n_users = 10

    users = rng.choice(ml_ds.users.ids(), n_users).tolist()

    algo = ImplicitMF(20, epochs=10, use_ratings=True)
    pipe = topn_pipeline(algo, n=10)
    pipe.train(ml_ds)
    assert algo.users_ is not None
    assert algo.user_features_ is not None
    # _log.debug("Items: " + str(items))

    correlations = pd.Series(np.nan, index=users)
    for u in users:
        recs = pipe.run("recommender", query=u)
        assert isinstance(recs, ItemList)
        user_data = ml_ds.user_row(u)
        assert user_data is not None
        upos = algo.users_.number(u)
        _log.info("user %s: %s ratings", u, len(user_data))

        _log.debug("user_features from fit: " + str(algo.user_features_[upos, :]))

        # get the user's rating series
        query = RecQuery(-1, user_data)

        new_recs = pipe.run("recommender", query=query)
        assert isinstance(new_recs, ItemList)

        # merge new & old recs
        old_scores = recs.scores("pandas", index="ids")
        assert old_scores is not None
        new_scores = new_recs.scores("pandas", index="ids")
        assert new_scores is not None

        old_scores, new_scores = old_scores.align(new_scores, join="outer", fill_value=-np.nan)

        tau = stats.kendalltau(old_scores, new_scores)
        _log.info("correlation for user %s: %f", u, tau.correlation)
        correlations.loc[u] = tau.correlation

    _log.debug("correlations: %s", correlations)

    assert not (any(correlations.isnull()))
    assert all(correlations >= 0.5)


def test_als_predict_bad_item():
    algo = ImplicitMF(20, epochs=10)
    algo.train(simple_ds)

    preds = algo(10, ItemList([4]))
    assert len(preds) == 1
    preds = preds.scores("pandas", index="ids")
    assert preds is not None
    assert preds.index[0] == 4
    assert np.isnan(preds.loc[4])


def test_als_predict_bad_user():
    algo = ImplicitMF(20, epochs=10)
    algo.train(simple_ds)

    preds = algo(50, ItemList([3]))
    assert len(preds) == 1
    preds = preds.scores("pandas", index="ids")
    assert preds is not None
    assert preds.index[0] == 3
    assert np.isnan(preds.loc[3])


def test_als_predict_no_user_features_basic(ml_ratings: pd.DataFrame, ml_ds: Dataset):
    np.random.seed(45)
    u = np.random.choice(ml_ds.users.ids(), 1)[0]
    items = np.random.choice(ml_ds.items.ids(), 2)

    algo = ImplicitMF(5, epochs=10)
    algo.train(ml_ds)
    preds = algo(u, ItemList(items))
    preds = preds.scores("pandas", index="ids")
    assert preds is not None

    user_data = ml_ds.user_row(u)

    algo_no_user_features = ImplicitMF(5, epochs=10, save_user_features=False)
    algo_no_user_features.train(ml_ds)
    query = RecQuery(u, user_data)
    preds_no_user_features = algo_no_user_features(query, ItemList(items))
    preds_no_user_features = preds_no_user_features.scores("pandas", index="ids")
    assert preds_no_user_features is not None

    assert algo_no_user_features.user_features_ is None
    assert preds_no_user_features.values == approx(preds, abs=0.1)
    diffs = np.abs(preds - preds_no_user_features)
    assert all(diffs <= 0.1)


@lktu.wantjit
def test_als_train_large(ml_ds: Dataset):
    algo = ImplicitMF(20, epochs=20, use_ratings=False)
    algo.train(ml_ds)

    assert algo.users_ is not None
    assert algo.user_features_ is not None
    assert len(algo.users_.index) == ml_ds.user_count
    assert len(algo.items_.index) == ml_ds.item_count
    assert algo.user_features_.shape == (ml_ds.user_count, 20)
    assert algo.item_features_.shape == (ml_ds.item_count, 20)


def test_als_save_load(tmp_path, ml_ds: Dataset):
    "Test saving and loading ALS models, and regularized training."
    algo = ImplicitMF(5, epochs=5, reg=(2, 1), use_ratings=False)
    algo.train(ml_ds)
    assert algo.users_ is not None

    fn = tmp_path / "model.bpk"
    with fn.open("wb") as pf:
        pickle.dump(algo, pf, protocol=pickle.HIGHEST_PROTOCOL)

    with fn.open("rb") as pf:
        restored = pickle.load(pf)

    assert torch.all(restored.user_features_ == algo.user_features_)
    assert torch.all(restored.item_features_ == algo.item_features_)
    assert np.all(restored.items_.index == algo.items_.index)
    assert np.all(restored.users_.index == algo.users_.index)


@lktu.wantjit
def test_als_train_large_noratings(ml_ds: Dataset):
    algo = ImplicitMF(20, epochs=20)
    algo.train(ml_ds)

    assert algo.users_ is not None
    assert algo.user_features_ is not None
    assert len(algo.users_.index) == ml_ds.user_count
    assert len(algo.items_.index) == ml_ds.item_count
    assert algo.user_features_.shape == (ml_ds.user_count, 20)
    assert algo.item_features_.shape == (ml_ds.item_count, 20)


@lktu.wantjit
def test_als_train_large_ratings(ml_ds):
    algo = ImplicitMF(20, epochs=20, use_ratings=True)
    algo.train(ml_ds)

    assert algo.users_ is not None
    assert algo.user_features_ is not None
    assert len(algo.users_.index) == ml_ds.user_count
    assert len(algo.items_.index) == ml_ds.item_count
    assert algo.user_features_.shape == (ml_ds.user_count, 20)
    assert algo.item_features_.shape == (ml_ds.item_count, 20)


@mark.slow
@mark.eval
def test_als_implicit_batch_accuracy(ml_100k):
    ds = from_interactions_df(ml_100k)
    results = quick_measure_model(ImplicitMF(25, epochs=20), ds)

    ndcg = results.list_summary().loc["NDCG", "mean"]
    _log.info("nDCG for users is %.4f", ndcg)
    assert ndcg > 0.22


@mark.slow
def test_als_match_orig(rng: np.random.Generator, ml_ds: Dataset):
    from lenskit.algorithms import als

    new = ImplicitMF(20, epochs=30, rng_spec=100)
    new.train(ml_ds)

    old = als.ImplicitMF(20, epochs=30, rng_spec=100)
    old.fit(ml_ds)

    for u in rng.choice(ml_ds.users.ids(), 50):
        items = rng.choice(ml_ds.items.ids(), 100)
        items = ItemList(items)

        n_scores = new(query=u, items=items)
        o_scores = old.predict_for_user(u, items.ids())

        assert np.all(n_scores.ids() == o_scores.index.values)
        assert n_scores.scores() == approx(o_scores.values)