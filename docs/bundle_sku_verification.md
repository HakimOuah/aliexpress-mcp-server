# Bundle SKU verification

`BUNDLE` economics must be based on the selected SKU, not only the listing title.

Statuses:

- `VERIFIED`: SKU properties explicitly describe bundle contents (`with Trimmer`, `kit`, etc.).
- `OPAQUE`: SKU label is only an opaque code such as `SET D` or `NO.2`; keep it for review and expose `sku_image_url`, but do not present bundle margin as verified.
- `MISMATCH`: the selected SKU clearly describes a product-only or accessory-only variant under a bundle-titled listing. Refine the supplier category from SKU semantics when possible.

This prevents price-floor accessory variants from creating false high-margin bundle opportunities.