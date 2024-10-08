import os
import re

# To use a consistent encoding
from codecs import open as copen

from setuptools import find_packages, setup

here = os.path.abspath(os.path.dirname(__file__))

# Get the long description from the relevant file
with copen(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()


def read(*parts):
    with copen(os.path.join(here, *parts), 'r') as fp:
        return fp.read()


def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]",
                              version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


__version__ = find_version("doors_detection_long_term", "__version__.py")

test_deps = [
    "pytest",
    "pytest-cov",
]

extras = {
    'test': test_deps,
}

setup(
    name='doors-detection-long-term',
    version=__version__,
    description="Doors detection long term",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/aislabunimi/door-detection-long-term",
    author="Michele Antonazzi",
    author_email="micheleantonazzi@gmail.com",
    # Choose your license
    license='Apache Licence 2.0',
    include_package_data=True,
    classifiers=[
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3'
    ],
    packages=find_packages(exclude=['contrib', 'docs', 'tests*']),
    tests_require=test_deps,
    # Add here the package dependencies
    install_requires=[
        'PyQt5',
        'numpy',
        'opencv-python',
        'generic-dataset',
        'torch',
        'torchvision',
        'scikit-learn',
        'detr @ git+http://github.com/micheleantonazzi/detr.git',
        'src @ git+http://github.com/micheleantonazzi/review_object_detection_metrics.git#egg=src',
        "pytest",
        "pytest-cov",
        'ipython',
        'pyyaml',
        'psutil',
        'tqdm',
        'seaborn',
        'openpyxl',
        'PyCuda',
        'tensorrt',
        'scikit-image',
        'trimesh'
    ],
    entry_points={
        'console_scripts': [
        ],
    },
    extras_require=extras,
)