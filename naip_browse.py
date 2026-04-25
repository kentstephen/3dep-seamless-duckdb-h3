# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "marimo",
#   "pystac-client",
#   "planetary-computer",
#   "shapely",
# ]
# ///

import marimo

__generated_with = "0.23.3"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    import pystac_client
    import planetary_computer
    from shapely.geometry import box, shape as shapely_shape

    return mo, planetary_computer, pystac_client


@app.cell
def _():
    #  Monument Valley Navajo Tribal Park 
    bbox = (-110.259297,36.874219,-109.937367,37.130223)
    # Mucch smaller MV
    # bbox = (-110.119667,36.965153,-110.054825,37.008449)
    return (bbox,)


@app.cell
def _(bbox, mo, planetary_computer, pystac_client):
    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    all_items = catalog.search(
        collections=["naip"],
        bbox=bbox,
        datetime="2003-01-01/2025-12-31",
        sortby="-datetime",
    ).item_collection()

    from collections import defaultdict

    # group by year, dedupe by quad bbox
    year_quads = defaultdict(dict)
    for it in all_items:
        year = it.datetime.year
        key = tuple(round(x, 4) for x in it.bbox)
        if key not in year_quads[year]:
            year_quads[year][key] = it

    years = sorted(year_quads.keys(), reverse=True)
    print(f"Total items: {len(all_items)}")
    for y in years:
        print(f"  {y}: {len(year_quads[y])} unique quads")

    mo.stop(not years, mo.callout(mo.md("No NAIP items found for this bbox."), kind="warn"))

    rows = []
    for y in years:
        quads = list(year_quads[y].values())
        dates = sorted({it.datetime.strftime("%Y-%m-%d") for it in quads})
        flag = "✓" if len(dates) == 1 else "⚠"
        rows.append(f"| {y} | {len(quads)} | {flag} | {', '.join(dates)} |")

    table_md = "| Year | Quads | | Capture dates |\n|------|-------|---|---------------|\n" + "\n".join(rows)
    mo.stop(False, mo.md(table_md))
    return year_quads, years


@app.cell
def _(mo, year_quads, years):
    year_picker = mo.ui.dropdown(
        options={f"{y}  ({len(year_quads[y])} quads)": y for y in years},
        value=f"{years[0]}  ({len(year_quads[years[0]])} quads)",
        label="NAIP year",
    )
    mo.md(f"### {len(years)} years found\n\nPick a year to preview:  {year_picker}")
    return (year_picker,)


@app.cell
def _(mo, year_picker, year_quads):
    selected_year = year_picker.value
    items = list(year_quads[selected_year].values())
    items.sort(key=lambda it: (it.bbox[1], it.bbox[0]))  # sort south→north, west→east

    dates = sorted({it.datetime.strftime("%Y-%m-%d") for it in items})
    date_summary = "  ·  ".join(dates)
    consistent = "✓ same date" if len(dates) == 1 else f"⚠ {len(dates)} different dates"

    tiles = [
        mo.vstack([
            mo.image(src=it.assets["rendered_preview"].href, width=400),
            mo.md(f"`{it.id}`  \n{it.datetime.strftime('%Y-%m-%d')}"),
        ])
        for it in items
    ]

    mo.vstack([
        mo.md(f"**{selected_year}** — {len(items)} quads — {consistent}\n\n{date_summary}"),
        mo.hstack(tiles, wrap=True),
    ])
    return


if __name__ == "__main__":
    app.run()
