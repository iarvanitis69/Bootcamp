1) Use the conda env interpreter phd_conda_env_p10 for this project
2) After changes on code 
   a) inform the README.md file
   b) create the README.pdf using the following command
   pandoc README.md -o README.pdf -V author="Arvanitis John"  --pdf-engine=xelatex   --toc --toc-depth=3   --number-sections   --highlight-style=tango   -V geometry:margin=0.8in   -V mainfont="DejaVu Serif"   -V sansfont="DejaVu Sans"   -V monofont="DejaVu Sans Mono"   -V colorlinks=true   -V linkcolor=blue   -V urlcolor=blue
3) ## Τεχνικές Απαιτήσεις (ισχύουν σε όλα τα Features)

a **Logging**: όλα τα logs στο **stdout** (όχι σε αρχεία), με timestamp,
   log level, και μήνυμα.
b **Error Handling**: `try/except` σε κάθε database ή Redis operation.
   Ποτέ silent failures — κάθε exception πρέπει να γίνεται `logger.error()`
   και να επιστρέφει `500` με `{"error": "database error"}` (ή ανάλογο).
c **Parameterized Queries**: **Ποτέ** string concatenation/f-string μέσα σε
   SQL με user input — πάντα bind parameters.
d **Secrets σε `.env`**: ποτέ credentials hardcoded στον κώδικα.
e **Health Check Endpoint** (`GET /health`): πρέπει να ελέγχει **και** τη
   βάση **και** το Redis ξεχωριστά, και να επιστρέφει `200` αν όλα είναι ok
   ή `503` αν κάτι είναι degraded, με λεπτομέρεια ανά component.

