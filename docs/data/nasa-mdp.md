# NASA MDP data provenance

MapVerity's defect-risk model trains on the NASA Metrics Data Program defect datasets, cleaned
by Shepperd, Song, Sun and Mair ("Data Quality: Some Comments on the NASA Software Defect
Datasets", IEEE Transactions on Software Engineering 39(9), 2013), version D''.

MapVerity **does not redistribute** these files. `mapverity datasets` and `mapverity risk train`
download them from a public mirror
(`github.com/klainfo/NASADefectDataset`, path `CleanedData/MDP/D''`) and refuse any file whose
SHA-256 differs from the values recorded in `src/mapverity/ai/datasets.py`:

| Project | Modules | Defective | SHA-256 (first 16) |
|---|---:|---:|---|
| CM1 | 327 | 42 | 870f523699f25bb7 |
| JM1 | 7,720 | 1,612 | 579060380b8d8cb1 |
| KC1 | 1,162 | 294 | 5cea7a3e02ff9954 |
| KC3 | 194 | 36 | f93b7f373101d9e6 |
| MC1 | 1,952 | 36 | d7a9047cf6195969 |
| MC2 | 124 | 44 | 6e837f0e08f3e795 |
| MW1 | 250 | 25 | 93e86aa4e87d5caf |
| PC1 | 679 | 55 | 9d1dd98f7f121206 |
| PC2 | 722 | 16 | 082bd53f6c16ddf8 |
| PC3 | 1,053 | 130 | 71e058433f948fe3 |
| PC4 | 1,270 | 176 | 4a55be821b0198c6 |
| PC5 | 1,694 | 458 | de171fb89c5e6ce3 |

The data originates from NASA's IV&V Facility Metrics Data Program. Check the terms that apply to
your use before redistributing it, and cite Shepperd et al. (2013) when you publish results.
