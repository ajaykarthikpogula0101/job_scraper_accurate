"""Check a delivered workbook for the defects the client reported.

    python rescrape\validate_output.py D:\Job_Hiring_Corrected.xlsx
    python rescrape\validate_output.py D:\Job_Hiring.xlsx        # the original, for comparison

Reports: job URLs / titles attached to several unrelated company groups,
career boards shared across unrelated groups, and Country vs job location.
"""
import sys
import pandas as pd

path = sys.argv[1]
df = pd.read_excel(path, sheet_name=0, dtype=str)
print("file:", path)
print("rows:", len(df), " companies (KEYID):", df["KEYID"].nunique(), " groups (ID_Company):", df["ID_Company"].nunique())

g = df.groupby("Job Title")["ID_Company"].nunique().sort_values(ascending=False)
print("\njob titles attached to >3 different company groups:", int((g > 3).sum()))
print(g.head(8).to_string())

if "Job URL" in df.columns:
    u = df[df["Job URL"].fillna("") != ""].groupby("Job URL")["ID_Company"].nunique().sort_values(ascending=False)
    print("\njob URLs attached to >1 company group:", int((u > 1).sum()), " (max groups per URL: %s)" % (u.max() if len(u) else 0))
    if (u > 1).sum():
        print(u.head(5).to_string())
if "Career Page URL" in df.columns:
    c = df[df["Career Page URL"].fillna("") != ""].drop_duplicates("KEYID").groupby("Career Page URL")["ID_Company"].nunique().sort_values(ascending=False)
    print("\ncareer boards shared by >1 company group:", int((c > 1).sum()))
    print(c.head(8).to_string())
if "Company Country" in df.columns:
    same = (df["Country"].fillna("").str.lower() == df["Company Country"].fillna("").str.lower())
    print("\nrows where Country == entity country: %d / %d" % (int(same.sum()), len(df)))
    print("Location Country Source:", df["Location Country Source"].value_counts().to_dict())
print("\nCountry:", df["Country"].value_counts().head(10).to_dict())
print("Seniority:", df["Seniority Level"].value_counts().to_dict())
print("blank description rows:", int((df["Job Description"].fillna("").str.strip() == "").sum()))
print("blank posted date rows:", int(df["Posted Date"].isna().sum()))
