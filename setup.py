"""Python setup.py for PROJECT_NAME package"""
from setuptools import find_packages, setup

setup(
    name="PROJECT_NAME",
    version="0.0.0",
    description="PROJECT_DESCRIPTION",
    url="https://github.com/bswck/PROJECT_NAME/",
    long_description_content_type="text/markdown",
    author="bswck",
    packages=find_packages(exclude=["tests", ".github"]),
    entry_points={
        "console_scripts": ["PROJECT_NAME = PROJECT_NAME.__main__:main"]
    },
    extras_require={"test": ["pytest"]},
)
