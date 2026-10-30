### ❌ Trayce eval: 1 regression

`support` v1 · 15 items × 3 samples · [this run](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96) vs [baseline](http://127.0.0.1:13000/p/default/experiments/862c4fd1-6701-4b5d-9ad0-c4f25459ead4) (at the merge-base `bd0e24a`)

| Scorer | Baseline | Current | Change (95% CI) | max_drop | Items | Status |
|---|---|---|---|---|---|---|
| `answer` | 100% | 100% | +0 pts (+0 pts to +0 pts) | 5 pts | 3 | ✅ ok |
| `steps` | 93% | 33% | -60 pts (-87 pts to -33 pts) | 5 pts | 15 | ❌ regressed |
| `few-steps` | 100% | 100% | +0 pts (+0 pts to +0 pts) | 5 pts | 15 | ✅ ok |

⚠️ Underpowered: the interval is wider than `max_drop`, so a drop that size could go undetected. More items or `samples` narrow it.

Cost per sample $0.0156 → $0.0142 · p50 latency 4.9 s → 4.7 s (not gated)

**Item regressions** (most samples passed before, most fail now; not gated)

- [item `996c1db3`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/996c1db3-3b88-4cdd-ac1d-ccf4597bdc6b) · `steps` 3/3 → 0/3
- [item `a3bce05f`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/a3bce05f-d683-4485-849d-bdd17c10b100) · `steps` 3/3 → 0/3
- [item `32674ccf`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/32674ccf-f8ba-4509-9e7d-227aee3ade77) · `steps` 3/3 → 0/3
- [item `9d6a9909`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/9d6a9909-a196-42cb-9b04-3313f656864f) · `steps` 3/3 → 0/3
- [item `91320bdb`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/91320bdb-01e5-41a3-a570-805bc9d02e74) · `steps` 3/3 → 0/3
- [item `701bdede`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/701bdede-3166-4a03-b7b2-1c2b4761be90) · `steps` 3/3 → 0/3
- [item `807ca8da`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/807ca8da-f40b-427e-af12-6dd7bb75cb5c) · `steps` 3/3 → 0/3
- [item `3b8057f0`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/3b8057f0-656e-41d1-a0ca-f0b1f4486860) · `steps` 3/3 → 0/3
- [item `c7b0e632`](http://127.0.0.1:13000/p/default/experiments/89e8f2b8-a540-4093-a758-f9f15e932f96/compare/862c4fd1-6701-4b5d-9ad0-c4f25459ead4/items/c7b0e632-56cc-412a-9208-62b5864608c0) · `steps` 3/3 → 0/3
