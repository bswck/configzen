# from io import StringIO
# from typing import Any
#
# from configzen import Engine
#
# import configparser
#
#
# class ConfigParserEngine(Engine):
#     name = 'configparser'
#
#     def __init__(self, parser_factory=configparser.ConfigParser, **options):
#         super().__init__(**options)
#         self.parser_factory = parser_factory
#
#     def load(self, blob, defaults=None):
#         if defaults is None:
#             defaults = {}
#         data = self.parser_factory(defaults=defaults)
#         for section in self.schema:
#             data.add_section(section)
#         data.read_string(blob)
#         return data
#
#     def _dump(self, config: dict[str, Any]):
#         data = self.parser_factory(**self.engine_options)
#         data.read_dict(config)
#         dump = StringIO()
#         data.write(dump)
#         return dump.read()
