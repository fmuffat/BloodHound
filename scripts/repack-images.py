#!/usr/bin/env python3
"""Rewrites a `docker save` archive with UNCOMPRESSED layers.

    scripts/repack-images.py images.tar images-raw.tar

Recent Docker (containerd image store) exports layers gzip-compressed, so
compressing the archive again gains nothing. With plain tar layers, one zstd
(or xz) pass over the whole archive finds the redundancy between layers and
images: ~25 % smaller (1.78 GB -> 1.34 GB for Bloodhound), which keeps the
OVA under GitHub's 2 GiB release-asset limit.

Each gzip layer blob is decompressed; its new digest is the layer's DiffID,
already listed in the image config, so configs are unchanged and image IDs
stay the same. Manifests, indexes, index.json and the legacy manifest.json
are rewritten to point to the new blobs. `docker load` accepts the result
(OCI and Docker media types for uncompressed layers).
"""
import gzip
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile

GZIP_MAGIC = b"\x1f\x8b"
UNCOMPRESSED = {
    "application/vnd.oci.image.layer.v1.tar+gzip": "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.docker.image.rootfs.diff.tar.gzip": "application/vnd.docker.image.rootfs.diff.tar",
}
MANIFEST_TYPES = {"application/vnd.oci.image.manifest.v1+json",
                  "application/vnd.docker.distribution.manifest.v2+json"}
INDEX_TYPES = {"application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json"}


def blob_path(root, digest):
    algo, hexd = digest.split(":", 1)
    return os.path.join(root, "blobs", algo, hexd)


def write_blob(root, data: bytes) -> tuple[str, int]:
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    path = blob_path(root, digest)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return digest, len(data)


def decompress_layer(root, digest, cache):
    """gzip blob -> plain tar blob; returns (new_digest, new_size) or None."""
    if digest in cache:
        return cache[digest]
    path = blob_path(root, digest)
    if not os.path.exists(path):
        cache[digest] = None
        return None
    with open(path, "rb") as f:
        if f.read(2) != GZIP_MAGIC:
            cache[digest] = None
            return None
    h = hashlib.sha256()
    size = 0
    tmp = path + ".raw"
    with gzip.open(path, "rb") as src, open(tmp, "wb") as dst:
        while chunk := src.read(4 << 20):
            h.update(chunk)
            dst.write(chunk)
            size += len(chunk)
    new = "sha256:" + h.hexdigest()
    os.replace(tmp, blob_path(root, new))
    if new != digest:
        os.remove(path)
    cache[digest] = (new, size)
    return cache[digest]


def rewrite(root, desc, layers, done):
    """Rewrites the manifest/index behind `desc`; returns the updated descriptor."""
    digest, mtype = desc["digest"], desc.get("mediaType", "")
    if digest in done:
        return {**desc, **done[digest]}
    path = blob_path(root, digest)
    if not os.path.exists(path) or not (mtype in MANIFEST_TYPES or mtype in INDEX_TYPES):
        return desc
    with open(path, "rb") as f:
        doc = json.load(f)
    changed = False
    if mtype in MANIFEST_TYPES:
        for layer in doc.get("layers", []):
            new = decompress_layer(root, layer["digest"], layers)
            if new:
                layer["digest"], layer["size"] = new
                layer["mediaType"] = UNCOMPRESSED.get(layer["mediaType"], layer["mediaType"])
                changed = True
    else:
        children = []
        for child in doc.get("manifests", []):
            new_child = rewrite(root, child, layers, done)
            changed |= new_child is not child and new_child != child
            children.append(new_child)
        doc["manifests"] = children
    if not changed:
        done[digest] = {}
        return desc
    new_digest, size = write_blob(root, json.dumps(doc, separators=(",", ":")).encode())
    done[digest] = {"digest": new_digest, "size": size}
    return {**desc, **done[digest]}


def main(src, dst):
    work = tempfile.mkdtemp(prefix="repack-", dir=os.path.dirname(os.path.abspath(dst)))
    try:
        with tarfile.open(src) as t:
            t.extractall(work, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(work)
        layers, done = {}, {}

        index_path = os.path.join(work, "index.json")
        with open(index_path) as f:
            index = json.load(f)
        index["manifests"] = [rewrite(work, d, layers, done) for d in index.get("manifests", [])]
        with open(index_path, "w") as f:
            json.dump(index, f, separators=(",", ":"))

        legacy_path = os.path.join(work, "manifest.json")
        if os.path.exists(legacy_path):
            with open(legacy_path) as f:
                legacy = json.load(f)
            for entry in legacy:
                new_layers = []
                for lp in entry.get("Layers", []):
                    old = "sha256:" + os.path.basename(lp)
                    new = layers.get(old) or decompress_layer(work, old, layers)
                    new_layers.append(f"blobs/sha256/{new[0].split(':', 1)[1]}" if new else lp)
                entry["Layers"] = new_layers
                entry.pop("LayerSources", None)
            with open(legacy_path, "w") as f:
                json.dump(legacy, f, separators=(",", ":"))

        # Drop blobs nothing references any more (old manifests/indexes)
        for old, new in done.items():
            if new and new.get("digest") != old and os.path.exists(blob_path(work, old)):
                os.remove(blob_path(work, old))

        with tarfile.open(dst, "w", format=tarfile.PAX_FORMAT) as t:
            for name in sorted(os.listdir(work)):
                t.add(os.path.join(work, name), arcname=name)
        n = sum(1 for v in layers.values() if v)
        print(f"repacked: {n} layers decompressed -> {dst}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
