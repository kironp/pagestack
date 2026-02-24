PageStack
===

Create an EPUB from a list of URLs so you can read web content offline on your favorite e-reader. I built it to bundle my favorite engineering blogs into books I can load on my Kindle.

## Setup

### Install as a package

```bash
pip install pagestack
```

### Run from source

1. Clone the repository and navigate into it.
2. Install the dependencies:

```bash
pip install -r requirements.txt
```

## Usage

1. Create a text file, for example `urls.txt`, and add one URL per line.
2. Run the script (if installed):

```bash
pagestack examples/urls.txt uber-schemaless.epub \
    --title "Uber Schemaless - Blogs" \
    --author "Uber Engineering"
```

If you do not pass any parameter and just run by passing `urls.txt`, the
script assumes sane defaults and generates an epub file.

3. If you have installed it from source, then you can run the following

```bash
python src/pagestack/main.py examples/urls.txt uber-schemaless.epub \
    --title "Uber Schemaless - Blogs" \
    --author "Uber Engineering"
```

## Publishing on PyPi

```bash
pip install twine
twine upload dist/*
```

## License

MIT
