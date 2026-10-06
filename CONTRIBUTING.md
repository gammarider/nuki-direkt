# Contributing to Nuki Direkt

Report bugs and propose changes through [GitHub](https://github.com/gammarider/nuki-direkt).
Include the Home Assistant and Nuki Direkt versions, device model and reproducible
steps. Remove PINs, pairing keys, tokens and personal data from all attachments.

## Development

Use Python 3.14 and an isolated environment:

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
python -m ruff check --isolated --select E9,F63,F7,F82 custom_components tests tools
```

The component lives in `custom_components/nuki_direkt`. Tests simulate Bluetooth;
they must not pair, move or reset real locks. Preserve existing entity and device
IDs when changing storage or migration code. Document hardware tests separately.

Start a branch from `main`, add meaningful tests and update the documentation.
Open a pull request describing the change and its validation. CI runs regression
tests, Ruff, Hassfest and HACS checks.

## License

Contributions are distributed under the project's MIT license. Preserve the
copyright and license notices of all incorporated code.
