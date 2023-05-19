import argparse

from configzen import ConfigMeta, ConfigModel
from configzen.config import get_context


class Store(ConfigModel):
    class Config(ConfigMeta):
        extra = ConfigMeta.Extra.allow


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", help="file to load from")
    p.add_argument("dest", help="file to save into")
    p.add_argument("--async", dest="use_async", action="store_true", help="use async")

    opt = p.parse_args()

    if opt.use_async:
        import asyncio

        store = asyncio.run(Store.load_async(opt.source))
        print(store)
        context = get_context(store)
        context.loader.resource = opt.dest
        asyncio.run(store.save_async())
    else:
        store = Store.load(opt.source)
        print(store)
        context = get_context(store)
        context.loader.resource = opt.dest
        store.save()
