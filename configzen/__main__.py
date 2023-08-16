import argparse

from configzen import ConfigMeta, ConfigModel
from configzen.model import get_context


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
        print(store)  # noqa: T201
        context = get_context(store)
        context.agent.resource = opt.dest
        asyncio.run(store.save_async())
    else:
        store = Store.load(opt.source)
        print(store)  # noqa: T201
        context = get_context(store)
        context.agent.resource = opt.dest
        store.save()
