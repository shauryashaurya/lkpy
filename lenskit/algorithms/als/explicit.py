from __future__ import annotations

import logging
from typing import Optional, cast

import numpy as np
import pandas as pd
import torch
from seedbank import SeedLike

from lenskit.algorithms.bias import Bias
from lenskit.data import sparse_ratings
from lenskit.parallel.chunking import WorkChunks
from lenskit.util.logging import pbh_update, progress_handle

from .common import ALSBase, TrainContext, TrainingData

_log = logging.getLogger(__name__)


class BiasedMF(ALSBase):
    """
    Biased matrix factorization trained with alternating least squares :cite:p:`Zhou2008-bj`.  This
    is a prediction-oriented algorithm suitable for explicit feedback data, using the alternating
    least squares approach to compute :math:`P` and :math:`Q` to minimize the regularized squared
    reconstruction error of the ratings matrix.

    It provides two solvers for the optimization step (the `method` parameter):

    ``'cd'`` (the default)
        Coordinate descent :cite:p:`Takacs2011-ix`, adapted for a separately-trained bias model and
        to use weighted regularization as in the original ALS paper :cite:p:`Zhou2008-bj`.
    ``'cholesky'``
        The original ALS :cite:p:`Zhou2008-bj`, using Cholesky decomposition
        to solve for the optimized matrices.
    ``'lu'``:
        Deprecated alias for ``'cholskey'``

    See the base class :class:`.MFPredictor` for documentation on
    the estimated parameters you can extract from a trained model.

    Args:
        features: the number of features to train
        epochs: the number of iterations to train
        reg: the regularization factor; can also be a tuple ``(ureg, ireg)`` to
            specify separate user and item regularization terms.
        damping: damping factor for the underlying bias.
        bias: the bias model.  If ``True``, fits a :class:`Bias` with
            damping ``damping``.
        rng_spec:
            Random number generator or state (see :func:`seedbank.numpy_rng`).
        progress: a :func:`tqdm.tqdm`-compatible progress bar function
    """

    timer = None

    features: int
    epochs: int
    reg: float | tuple[float, float]
    bias: Bias | None
    rng: np.random.Generator
    save_user_features: bool

    def __init__(
        self,
        features: int,
        *,
        epochs: int = 10,
        reg: float | tuple[float, float] = 0.1,
        damping: float = 5,
        bias: bool | Bias = True,
        rng_spec: Optional[SeedLike] = None,
        save_user_features: bool = True,
    ):
        super().__init__(
            features,
            epochs=epochs,
            reg=reg,
            rng_spec=rng_spec,
            save_user_features=save_user_features,
        )
        if bias is True:
            self.bias = Bias(damping=damping)
        else:
            self.bias = bias or None

    @property
    def logger(self):
        return _log

    def prepare_data(self, ratings: pd.DataFrame):
        # transform ratings using offsets
        if self.bias:
            _log.info("[%s] normalizing ratings", self.timer)
            ratings = self.bias.fit_transform(ratings)

        rmat, users, items = sparse_ratings(ratings, torch=True)
        return TrainingData.create(users, items, rmat)

    def als_half_epoch(self, epoch: int, context: TrainContext):
        chunks = WorkChunks.create(context.nrows)
        with progress_handle(
            _log, f"epoch {epoch} {context.label}s", total=context.nrows, unit="row"
        ) as pbh:
            return _train_update_fanout(context, chunks, pbh)

    def new_user_embedding(self, user, ratings: pd.Series) -> tuple[torch.Tensor, float | None]:
        u_offset = None
        if self.bias:
            ratings, u_offset = self.bias.transform_user(ratings)
        ratings = cast(pd.Series, ratings)

        ri_idxes = self.item_index_.get_indexer_for(ratings.index)
        ri_good = ri_idxes >= 0
        ri_it = torch.from_numpy(ri_idxes[ri_good])
        ri_val = torch.from_numpy(ratings.values[ri_good])

        # unpack regularization
        if isinstance(self.reg, tuple):
            ureg, ireg = self.reg
        else:
            ureg = self.reg

        u_feat = _train_bias_row_cholesky(ri_it, ri_val, self.item_features_, ureg)
        return u_feat, u_offset

    def finalize_scores(self, user, scores: torch.Tensor, u_offset: float | None) -> torch.Tensor:
        if self.bias and u_offset is not None:
            return self.bias.inverse_transform_user(user, scores, u_offset)
        elif self.bias:
            return self.bias.inverse_transform_user(user, scores)
        else:
            return scores

    def __str__(self):
        return "als.BiasedMF(features={}, regularization={})".format(self.features, self.reg)


@torch.jit.script
def _train_solve_row(
    cols: torch.Tensor,
    vals: torch.Tensor,
    this: torch.Tensor,
    other: torch.Tensor,
    regI: torch.Tensor,
) -> torch.Tensor:
    nf = this.shape[1]
    M = other[cols, :]
    MMT = M.T @ M
    # assert MMT.shape[0] == ctx.n_features
    # assert MMT.shape[1] == ctx.n_features
    A = MMT + regI * len(cols)
    V = M.T @ vals
    V = V.reshape(1, nf, 1)
    # and solve
    L, info = torch.linalg.cholesky_ex(A)
    if int(info):
        raise RuntimeError("error computing Cholesky decomposition (not symmetric?)")
    V = torch.cholesky_solve(V, L).reshape(nf)
    return V


@torch.jit.script
def _train_update_rows(ctx: TrainContext, start: int, end: int, pbh: str) -> torch.Tensor:
    result = ctx.left[start:end, :].clone()

    for i in range(start, end):
        row = ctx.matrix[i]
        (n,) = row.shape
        if n == 0:
            continue

        cols = row.indices()[0]
        vals = row.values().type(ctx.left.type())

        V = _train_solve_row(cols, vals, ctx.left, ctx.right, ctx.regI)
        result[i - start] = V
        pbh_update(pbh, 1)

    return result


@torch.jit.script
def _train_update_fanout(ctx: TrainContext, chunking: WorkChunks, pbh: str) -> float:
    if ctx.nrows <= 50:
        # at 50 rows, we run sequentially
        M = _train_update_rows(ctx, 0, ctx.nrows, pbh)
        sqerr = torch.norm(ctx.left - M)
        ctx.left[:, :] = M
        return sqerr.item()

    results: list[tuple[int, int, torch.jit.Future[torch.Tensor]]] = []
    for start in range(0, ctx.nrows, chunking.chunk_size):
        end = min(start + chunking.chunk_size, ctx.nrows)
        results.append((start, end, torch.jit.fork(_train_update_rows, ctx, start, end, pbh)))  # type: ignore

    sqerr = torch.tensor(0.0)
    for start, end, r in results:
        M = r.wait()
        diff = (ctx.left[start:end, :] - M).ravel()
        sqerr += torch.dot(diff, diff)
        ctx.left[start:end, :] = M

    return sqerr.sqrt().item()


def _train_bias_row_cholesky(
    items: torch.Tensor, ratings: torch.Tensor, other: torch.Tensor, reg: float
) -> torch.Tensor:
    """
    Args:
        items: the item IDs the user has rated
        ratings: the user's (normalized) ratings for those items
        other: the item-feature matrix
        reg: the regularization term
    Returns:
        the user-feature vector (equivalent to V in the current Cholesky code)
    """
    M = other[items, :]
    nf = other.shape[1]
    regI = torch.eye(nf, device=other.device) * reg
    MMT = M.T @ M
    A = MMT + regI * len(items)

    V = M.T @ ratings
    L, info = torch.linalg.cholesky_ex(A)
    if int(info):
        raise RuntimeError("error computing Cholesky decomposition (not symmetric?)")
    V = V.reshape(1, nf, 1)
    V = torch.cholesky_solve(V, L).reshape(nf)

    return V