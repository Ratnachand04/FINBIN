from setuptools import find_packages, setup

setup(
    name="crypto-intelligence-terminal",
    version="0.1.0",
    description="Self-hosted crypto trading intelligence terminal",
    packages=find_packages(exclude=("tests", "docs")),
    include_package_data=True,
    python_requires=">=3.11",
)
