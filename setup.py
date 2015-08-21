#!/usr/bin/env python

from setuptools import find_packages, setup

setup(
    name='alertlib',
    version='1.0',
    author='Khan Academy',
    license='Proprietary',
    packages=find_packages(),
    scripts=['alert.py'],
    description='Khan Academy alert library',
    long_description='\n' + open('README.md').read(),
)
