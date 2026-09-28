"""Turn the re-scrape output into the client's Job_Hiring layout.

    python rescrape\export_job_hiring.py --scrape rescrape\full_run.csv ^
        --companies rescrape\job_hiring_companies.csv ^
        --original D:\Job_Hiring.xlsx --out D:\Job_Hiring_Corrected.xlsx

Every delivered row is one posting scraped from the company's OWN verified
career portal / ATS board.  Country is the country of the posting's location
(falls back to the company's registered country only when the location is
blank or unrecognisable, and says so in Location Country Source).
"""
import argparse
import csv
import os
import re
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from job_scraper.leadership import is_leadership_title  # noqa: E402
from location_country import location_country  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 10 * 1024 * 1024))

ORIGINAL_COLUMNS = [
    "KEYID", "ID_Company", "DUNS_NUMBER", "Total openings", "PARTY_NAME", "Job Title",
    "Posted Date", "Deleted/Closed Date", "Education Stream", "Education Type",
    "Education Qualification", "Years of Experience", "Seniority Level", "Employment Type",
    "Skills", "Job Description", "Country", "Job Status", "Salary_USD", "Min_Salary_USD",
    "Max_Salary_USD",
]
EXTRA_COLUMNS = [
    "Job Location", "Location Country Source", "Company Country", "Job URL",
    "Career Page URL", "Career Page Source", "Salary (as posted)", "Currency", "Scraped At",
]

_EDU_TYPE = {
    "Bachelor": "Bachelor's Degree", "Master": "Master's Degree", "Doctorate": "Doctorate",
    "Associate Degree": "Associate Degree", "Diploma": "Diploma", "Certification": "Certification",
    "High School": "High School", "Vocational": "Vocational Training",
}
_EMPLOYMENT = {
    "full time": "Full Time", "full-time": "Full Time", "fulltime": "Full Time", "regular": "Full Time",
    "part time": "Part Time", "part-time": "Part Time", "contract": "Contract", "contractor": "Contract",
    "temporary": "Contract", "fixed term": "Contract", "internship": "Internship", "intern": "Internship",
    "apprenticeship": "Apprenticeship", "permanent": "Permanent", "volunteer": "OTHER", "per diem": "OTHER",
    "prn": "OTHER", "seasonal": "Contract",
}


_NOT_OWNER = re.compile(
    r"\b(?:process|product|service|data|risk|system|application|platform|feature|"
    r"epic|story|control|capability|workflow|business\s+process|technical|solution)\s+owner\b", re.I)


def seniority_level(title):
    t = _NOT_OWNER.sub(" ", (title or "").lower())
    if re.search(r"\bchief\b|\b(ceo|cfo|coo|cto|cio|cmo|chro|ciso|cro|cpo|cdo)\b|\bpresident\b|\bfounder\b|\bowner\b|\bproprietor\b", t) \
            and not re.search(r"\bvice\s+president\b", t):
        return "C-Level"
    if re.search(r"\bvice\s+president\b|\bvp\b|\bsvp\b|\bevp\b|\bavp\b", t):
        return "V-Level"
    if re.search(r"\bdirector\b|\bmanaging\s+director\b|\bmanaging\s+partner\b", t):
        return "D-Level"
    if re.search(r"\bhead\s+of\b|\bhead\b|\bgeneral\s+manager\b", t):
        return "H-Level"
    if re.search(r"\bsenior\s+manager\b|\bprincipal\b|\bpartner\b|\bsr\.?\s+manager\b", t):
        return "M-Level"
    return ""


def yyyymmdd(value):
    v = str(value or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return int(m.group(1) + m.group(2) + m.group(3))
    m = re.match(r"^(\d{8})$", v)
    if m:
        return int(m.group(1))
    return None


def years_of_experience(minimum, maximum):
    def num(x):
        try:
            x = float(str(x).strip())
            return x if x > 0 else None
        except (TypeError, ValueError):
            return None
    lo, hi = num(minimum), num(maximum)
    if lo and hi and hi >= lo:
        val = (lo + hi) / 2.0
    else:
        val = lo or hi
    if not val:
        return ""
    return ("%g Years" % val) if val != int(val) else ("%d Years" % int(val))


def total_openings_bucket(n):
    try:
        n = int(float(n))
    except (TypeError, ValueError):
        return ""
    if n >= 2000:
        return "2000+"
    if n >= 1000:
        return "1000+"
    if n >= 100:
        return "%d+" % ((n // 100) * 100)
    if n >= 10:
        return "%d+" % ((n // 5) * 5)
    return str(n)


def usd_salary(salary, minimum, maximum, currency):
    text = str(salary or "")
    cur = str(currency or "").strip().upper()
    is_usd = cur in ("USD", "US$", "$") or (not cur and "$" in text and not re.search(r"(?:A|C|S|NZ|HK|R|MX)\$", text))

    def num(x):
        try:
            x = float(str(x).replace(",", "").strip())
            return x if x > 0 else None
        except (TypeError, ValueError):
            return None
    lo, hi = num(minimum), num(maximum)
    if not is_usd or not (lo or hi):
        return "", None, None
    lo = lo or hi
    hi = hi or lo
    fmt = lambda v: ("%d" % v) if v == int(v) else ("%.2f" % v)
    return "$%s - $%s" % (fmt(lo), fmt(hi)), lo, hi


def employment_type(value):
    v = str(value or "").strip()
    if not v:
        return ""
    key = v.lower()
    for k, label in _EMPLOYMENT.items():
        if k in key:
            return label
    return v[:40]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrape", required=True, nargs="+", help="scraper output CSV(s)")
    ap.add_argument("--companies", default=os.path.join(ROOT, "rescrape", "job_hiring_companies.csv"))
    ap.add_argument("--original", default=r"D:\Job_Hiring.xlsx")
    ap.add_argument("--out", default=r"D:\Job_Hiring_Corrected.xlsx")
    ap.add_argument("--all-out", default="", help="optional CSV with EVERY scraped posting (not just leadership)")
    ap.add_argument("--desc-max", type=int, default=4000)
    args = ap.parse_args()

    companies = pd.read_csv(args.companies, dtype=str, keep_default_na=False)
    companies["_key"] = companies["COMPANY_NAME"].str.strip().str.lower() + "|" + companies["WEBSITE"].str.strip().str.lower()

    frames = []
    for path in args.scrape:
        frames.append(pd.read_csv(path, dtype=str, keep_default_na=False, engine="python"))
    scrape = pd.concat(frames, ignore_index=True)
    scrape["_key"] = scrape["company_name"].str.strip().str.lower() + "|" + scrape["website"].str.strip().str.lower()
    # one row per company for the coverage sheet
    latest = scrape.drop_duplicates("_key", keep="last")

    merged = scrape.merge(companies, on="_key", how="inner", suffixes=("", "_c"))
    print("scrape rows: %d  matched to companies: %d  companies covered: %d / %d"
          % (len(scrape), len(merged), merged["KEYID"].nunique(), len(companies)))

    jobs = merged[merged["job_title"].str.strip() != ""].copy()
    jobs = jobs.drop_duplicates(["KEYID", "job_url", "job_title"])
    print("postings: %d across %d companies" % (len(jobs), jobs["KEYID"].nunique()))

    rows = []
    for r in jobs.itertuples(index=False):
        d = r._asdict()
        loc = d.get("job_location", "")
        country = location_country(loc)
        source = "job location"
        if not country:
            country = d.get("COUNTRY") or d.get("PARTY_COUNTRY") or ""
            source = "company country (location blank)" if not str(loc).strip() else "company country (location unrecognised)"
        sal_text, sal_lo, sal_hi = usd_salary(d.get("salary"), d.get("min_salary"), d.get("max_salary"), d.get("currency"))
        desc = str(d.get("job_description_clean") or d.get("job_description") or "")[:args.desc_max]
        rows.append({
            "KEYID": d["KEYID"], "ID_Company": d["ID_Company"], "DUNS_NUMBER": d["DUNS_NUMBER"],
            "Total openings": total_openings_bucket(d.get("num_positions")),
            "PARTY_NAME": d["PARTY_NAME"], "Job Title": d["job_title"].strip(),
            "Posted Date": yyyymmdd(d.get("posted_date")), "Deleted/Closed Date": yyyymmdd(d.get("closed_date")),
            "Education Stream": d.get("education_stream", ""),
            "Education Type": _EDU_TYPE.get(d.get("education_type", ""), d.get("education_type", "")),
            "Education Qualification": d.get("education_qualification", ""),
            "Years of Experience": years_of_experience(d.get("years_of_experience_min"), d.get("years_of_experience_max")),
            "Seniority Level": seniority_level(d["job_title"]),
            "Employment Type": employment_type(d.get("employment_type")),
            "Skills": d.get("skills", ""), "Job Description": desc, "Country": country,
            "Job Status": "Active" if (d.get("job_status") or "Active") == "Active" else d.get("job_status"),
            "Salary_USD": sal_text, "Min_Salary_USD": sal_lo, "Max_Salary_USD": sal_hi,
            "Job Location": loc, "Location Country Source": source,
            "Company Country": d.get("COUNTRY") or d.get("PARTY_COUNTRY") or "",
            "Job URL": d.get("job_url", ""), "Career Page URL": d.get("career_page_url", ""),
            "Career Page Source": "%s / %s" % (d.get("website_discovery", ""), d.get("career_page_discovery_method", "")),
            "Salary (as posted)": d.get("salary", ""), "Currency": d.get("currency", ""),
            "Scraped At": d.get("scraped_at", ""),
            "_lead": is_leadership_title(d["job_title"]),
        })
    out = pd.DataFrame(rows, columns=ORIGINAL_COLUMNS + EXTRA_COLUMNS + ["_lead"])
    all_lead = out[out["_lead"]].drop(columns="_lead").sort_values(["PARTY_NAME", "Job Title"])
    # A legal entity is registered in one country.  A posting located in a
    # different country belongs to a sister entity, not to this one (the
    # original file listed Japan postings under a Canadian subsidiary, which
    # is what the client flagged).  Postings whose location is blank, remote or
    # unrecognisable stay with the entity, marked in Location Country Source.
    same = (all_lead["Country"].str.strip().str.lower() == all_lead["Company Country"].str.strip().str.lower()) \
        | (all_lead["Location Country Source"] != "job location")
    lead = all_lead[same]
    other = all_lead[~same]
    print("leadership postings: %d (in entity country: %d, other country: %d) across %d companies"
          % (len(all_lead), len(lead), len(other), all_lead["KEYID"].nunique()))

    # coverage sheet: every input company, what was found, where
    cov = companies.merge(latest[["_key", "job_status", "career_page_url", "career_page_status",
                                  "career_page_discovery_method", "resolved_website", "website_discovery",
                                  "num_positions"]], on="_key", how="left")
    counts = out.groupby("KEYID").size().rename("Postings Scraped")
    lead_counts = lead.groupby("KEYID").size().rename("Leadership Postings")
    cov = cov.merge(counts, left_on="KEYID", right_index=True, how="left") \
             .merge(lead_counts, left_on="KEYID", right_index=True, how="left")
    cov["Postings Scraped"] = cov["Postings Scraped"].fillna(0).astype(int)
    cov["Leadership Postings"] = cov["Leadership Postings"].fillna(0).astype(int)
    cov["Scrape Status"] = cov["job_status"].fillna("NOT PROCESSED")
    cov.loc[cov["Postings Scraped"] > 0, "Scrape Status"] = "Jobs Found"
    cov = cov.rename(columns={"COMPANY_NAME": "Input Company Name", "COUNTRY": "Company Country",
                              "WEBSITE": "Input Website", "resolved_website": "Resolved Website",
                              "website_discovery": "Website Source", "career_page_url": "Career Page URL",
                              "career_page_discovery_method": "Career Page Source",
                              "num_positions": "Positions On Board"})
    cov = cov[["KEYID", "ID_Company", "DUNS_NUMBER", "PARTY_NAME", "Input Company Name", "Company Country",
               "Input Website", "Resolved Website", "Website Source", "Career Page URL", "Career Page Source",
               "Scrape Status", "Positions On Board", "Postings Scraped", "Leadership Postings"]]

    # summary
    orig = pd.read_excel(args.original, dtype=str) if os.path.exists(args.original) else pd.DataFrame()
    summary = pd.DataFrame([
        ("Unique companies in original file", len(companies)),
        ("Companies processed", int((cov["Scrape Status"] != "NOT PROCESSED").sum())),
        ("Companies with postings on their own career portal", int((cov["Postings Scraped"] > 0).sum())),
        ("Companies with leadership postings", int((cov["Leadership Postings"] > 0).sum())),
        ("Companies: career page found, no postings / unsupported", int(cov["Scrape Status"].str.contains("Unsupported|No Jobs", na=False).sum())),
        ("Companies: no verifiable career page / website", int(cov["Scrape Status"].str.contains("Not Found|Unreachable", na=False).sum())),
        ("Total postings scraped", len(out)),
        ("Leadership postings, all countries", len(all_lead)),
        ("Leadership postings delivered (sheet Job_Hiring: located in the entity's country)", len(lead)),
        ("Leadership postings located in another country (sheet Other_Country_Postings)", len(other)),
        ("Rows in original file", len(orig)),
        ("Country taken from job location", int((lead["Location Country Source"] == "job location").sum())),
        ("Country fell back to company country", int((lead["Location Country Source"] != "job location").sum())),
    ], columns=["Metric", "Value"])
    print(summary.to_string(index=False))

    with pd.ExcelWriter(args.out, engine="openpyxl") as xw:
        lead.to_excel(xw, sheet_name="Job_Hiring", index=False)
        other.to_excel(xw, sheet_name="Other_Country_Postings", index=False)
        cov.sort_values("PARTY_NAME").to_excel(xw, sheet_name="Company_Coverage", index=False)
        summary.to_excel(xw, sheet_name="Summary", index=False)
    print("wrote", args.out)
    if args.all_out:
        out.drop(columns="_lead").to_csv(args.all_out, index=False, encoding="utf-8-sig")
        print("wrote", args.all_out, len(out), "rows")


if __name__ == "__main__":
    main()
