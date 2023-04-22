import asyncio
import dataclasses
import configzen as zen


@dataclasses.dataclass
class Item(zen.Dataclass):
    counter: int
    exitmsg: str


async def main():
    config = await zen.Config(
        'test.yaml',
        dispatcher=zen.SimpleDispatcher(item=Item),
        asynchronous=True,
    )
    config.item.counter += 1
    config.item.exitmsg = 'something happened asynchronously!'
    await config.save()


asyncio.run(main())

