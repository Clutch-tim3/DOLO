# In 05_build_dataset.py:
# Replace hardcoded 2019/2020 with adaptive year split logic:

"""
    years = [r[0] for r in year_dist if r[0] is not None]
    min_year = min(years) if years else 2016
    max_year = max(years) if years else 2024

    if min_year <= 2019 and max_year >= 2021:
        train_end_year = 2019
        val_end_year = 2020
    else:
        # Dynamic split based on available year span
        span = max_year - min_year
        train_end_year = min_year + int(span * 0.6)
        val_end_year = min_year + int(span * 0.8)
        if val_end_year <= train_end_year:
            val_end_year = train_end_year + 1
"""
