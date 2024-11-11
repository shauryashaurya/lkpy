# This file is part of LensKit.
# Copyright (C) 2018-2023 Boise State University
# Copyright (C) 2023-2024 Drexel University
# Licensed under the MIT license, see LICENSE.md for details.
# SPDX-License-Identifier: MIT

import numpy as np
import pandas as pd

from pytest import approx, mark, raises

from lenskit.data import ItemList, from_interactions_df
from lenskit.metrics import call_metric
from lenskit.metrics.predict import MAE, RMSE, measure_user_predictions


def test_rmse_one():
    rmse = call_metric(RMSE, ItemList(["a"], scores=[1]), ItemList(["a"], rating=[1]))
    assert isinstance(rmse, float)
    assert rmse == approx(0)

    rmse = call_metric(RMSE, ItemList(["a"], scores=[1]), ItemList(["a"], rating=[2]))
    assert rmse == approx(1)

    rmse = call_metric(RMSE, ItemList(["a"], scores=[1]), ItemList(["a"], rating=[0.5]))
    assert rmse == approx(0.5)


def test_rmse_two():
    rmse = call_metric(
        RMSE, ItemList(["a", "b"], scores=[1, 2]), ItemList(["a", "b"], rating=[1, 2])
    )
    assert isinstance(rmse, float)
    assert rmse == approx(0)

    rmse = call_metric(
        RMSE, ItemList(["a", "b"], scores=[1, 1]), ItemList(["a", "b"], rating=[2, 2])
    )
    assert rmse == approx(1)

    rmse = call_metric(
        RMSE, ItemList(["a", "b"], scores=[1, 3]), ItemList(["a", "b"], rating=[3, 1])
    )
    assert rmse == approx(2)


def test_rmse_series_subset_items():
    rmse = call_metric(
        RMSE,
        ItemList(scores=[1, 3], item_ids=["a", "c"]),
        ItemList(rating=[3, 4, 1], item_ids=["a", "b", "c"]),
        missing_scores="ignore",
    )
    assert rmse == approx(2)


def test_rmse_series_missing_value_error():
    with raises(ValueError):
        call_metric(
            RMSE,
            ItemList(scores=[1, 3], item_ids=["a", "d"]),
            ItemList(rating=[3, 4, 1], item_ids=["a", "b", "c"]),
        )


def test_rmse_series_missing_value_ignore():
    rmse = call_metric(
        RMSE,
        ItemList(scores=[1, 3], item_ids=["a", "d"]),
        ItemList(rating=[3, 4, 1], item_ids=["a", "b", "c"]),
        missing_scores="ignore",
        missing_truth="ignore",
    )
    assert rmse == approx(2)


def test_mae_two():
    mae = call_metric(MAE, ItemList(["a", "b"], scores=[1, 2]), ItemList(["a", "b"], rating=[1, 2]))
    assert isinstance(mae, float)
    assert mae == approx(0)

    mae = call_metric(MAE, ItemList(["a", "b"], scores=[1, 1]), ItemList(["a", "b"], rating=[2, 2]))
    assert mae == approx(1)

    mae = call_metric(MAE, ItemList(["a", "b"], scores=[1, 3]), ItemList(["a", "b"], rating=[3, 1]))
    assert mae == approx(2)

    mae = call_metric(MAE, ItemList(["a", "b"], scores=[1, 3]), ItemList(["a", "b"], rating=[3, 2]))
    assert mae == approx(1.5)


@mark.slow
@mark.eval
def test_batch_rmse(ml_100k):
    import lenskit.algorithms.bias as bs
    import lenskit.batch as batch
    import lenskit.crossfold as xf

    algo = bs.Bias(damping=5)

    def eval(train, test):
        algo.fit(from_interactions_df(train))
        preds = batch.predict(algo, test, n_jobs=1)
        return preds.set_index(["user", "item"])

    results = pd.concat(
        (eval(train, test) for (train, test) in xf.partition_users(ml_100k, 5, xf.SampleN(5)))
    )

    user_rmse = measure_user_predictions(results, RMSE)

    # we should have all users
    users = ml_100k.user.unique()
    assert len(user_rmse) == len(users)
    missing = np.setdiff1d(users, user_rmse.index)
    assert len(missing) == 0

    # we should not have any missing values
    assert all(user_rmse.notna())

    # we should have a reasonable mean
    assert user_rmse.mean() == approx(0.93, abs=0.05)
