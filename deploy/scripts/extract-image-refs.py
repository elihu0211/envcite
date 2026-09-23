#!/usr/bin/env python3
"""Print every image reference in a rendered manifest stream, classified.

Output is one `kind<TAB>reference` line per image, where kind is `own` for images
this repository builds and publishes and `third-party` for everything else.

Why this exists: `kustomize build` and `kubeconform` both pass on a manifest that
names an image which does not exist. Two such references reached `main` in this
repository — `paradedb-extension:0.20.1-18-trixie` (that repository only ever
published 0.25.x) and `pgvector:0.8.1-18-minimal-trixie` (the `-minimal-` variant
exists for the Postgres base image, not for the extension images). Neither tool
contacts a registry, so nothing was red. The failure surfaces at pull time, in a
cluster, as ImagePullBackOff.

The classification exists because the two kinds have different truth conditions. A
third-party tag either exists now or the manifest is wrong. An own-image tag is
produced by a build job, so its absence before that job exists says nothing about
the manifest — checking it would make the gate fail for a reason it cannot fix.

The keys are listed rather than inferred. A recursive hunt for anything that looks
like an image reference finds strings that are not images and, worse, quietly stops
finding ones whose shape changes. When CloudNativePG or any other CRD introduces a
new key, this list must be extended — and the extension is visible in review.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import yaml

#: Scalar keys whose value is an image reference.
SCALAR_KEYS = frozenset(
    {
        "image",  # core/v1 Container
        "imageName",  # postgresql.cnpg.io/v1 Cluster
        "reference",  # .spec.postgresql.extensions[].image
    }
)


def walk(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            # `image:` is a mapping under CNPG extensions and a string on a
            # container. Taking only strings keeps both cases correct without
            # special-casing either.
            if key in SCALAR_KEYS and isinstance(value, str):
                out.append(value)
            else:
                walk(value, out)
    elif isinstance(node, list):
        for item in node:
            walk(item, out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", help="rendered manifest stream (kustomize build output)")
    parser.add_argument(
        "--own-prefix",
        action="append",
        default=[],
        help="reference prefix built by this repository; repeatable",
    )
    args = parser.parse_args()

    with open(args.manifests, encoding="utf-8") as fh:
        docs = list(yaml.safe_load_all(fh))

    refs: list[str] = []
    for doc in docs:
        walk(doc, refs)

    if not refs:
        # An empty result is indistinguishable from "every image checked out" once
        # the caller loops over the output. A manifest set with no images at all is
        # not a state this repository has, so treat it as a broken extractor.
        print("no image references found — the key list is probably stale", file=sys.stderr)
        return 1

    for ref in sorted(set(refs)):
        own = any(ref.startswith(p) for p in args.own_prefix if p)
        print(f"{'own' if own else 'third-party'}\t{ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
