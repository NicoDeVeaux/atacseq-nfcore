#!/usr/bin/env python3
"""Preserve comparison identities and numeric fingerprint exports in MultiQC 1.13."""


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
    from multiqc.modules.base_module import BaseMultiqcModule
    from multiqc.utils import util_functions

    if multiqc.__version__ != "1.13":
        raise RuntimeError("Revalidate the fingerprint workarounds before changing MultiQC 1.13")

    clean_s_name = BaseMultiqcModule.clean_s_name

    def clean_fingerprint_name(self, s_name, f=None, root=None, filename=None, seach_pattern_key=None):
        details = f if isinstance(f, dict) else {}
        effective_filename = filename if filename is not None else details.get("fn")
        effective_pattern = seach_pattern_key if seach_pattern_key is not None else details.get("sp_key")
        filename_selected = effective_filename is not None and (
            config.use_filename_as_sample_name is True
            or (
                isinstance(config.use_filename_as_sample_name, list)
                and effective_pattern is not None
                and effective_pattern in config.use_filename_as_sample_name
            )
        )
        context = (f, root, filename, seach_pattern_key)
        comparison_control = (
            details.get("sp_key") == "deeptools/plotFingerprintOutRawCounts"
            and isinstance(s_name, str)
            and s_name.startswith("control::")
            and s_name.endswith(".mLb.clN")
            and not filename_selected
        )
        if comparison_control and config.fn_clean_sample_names:
            # Filename truncation can collapse these identities, including empty-name fallbacks.
            cleaning = config.fn_clean_sample_names
            config.fn_clean_sample_names = False
            try:
                return clean_s_name(self, s_name[: -len(".mLb.clN")], *context)
            finally:
                config.fn_clean_sample_names = cleaning
        return clean_s_name(self, s_name, *context)

    BaseMultiqcModule.clean_s_name = clean_fingerprint_name
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
