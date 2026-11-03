#!/usr/bin/env python3
"""Preserve numeric fingerprint fields when exporting reports with MultiQC 1.13."""


def normalize_numeric_keys(data):
    normalized = {}
    for sample, fields in data.items():
        row = {}
        for field, value in fields.items():
            key = str(field)
            if key in row:
                raise ValueError(f"Fingerprint fields collide after conversion: {sample}/{key}")
            row[key] = value
        normalized[sample] = row
    return normalized


def main():
    import multiqc
    from multiqc.__main__ import run_multiqc
    from multiqc import config
    from multiqc.utils import util_functions

    if multiqc.__version__ != "1.13":
        raise RuntimeError("Revalidate the fingerprint export workaround before changing MultiQC 1.13")

    write_data_file = util_functions.write_data_file

    def write_fingerprint_data(data, fn, sort_cols=False, data_format=None):
        selected_format = config.data_format if data_format is None else data_format
        fingerprint = fn == "deeptools_plot_fingerprint_counts" or fn.startswith(
            "deeptools_plot_fingerprint_counts_"
        )
        if fingerprint and selected_format not in ("json", "yaml"):
            data = normalize_numeric_keys(data)
        return write_data_file(data, fn, sort_cols, data_format)

    util_functions.write_data_file = write_fingerprint_data
    run_multiqc()


if __name__ == "__main__":
    main()
