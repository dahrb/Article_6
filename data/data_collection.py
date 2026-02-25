"""
Script to collect Art. 6 decisions and judgments through the HUDOC Rest API

Last Updated:
17.02.26

History:
v1_0 - retrieves and sorts the cases into judgments/decisions
"""

import pandas as pd
import requests
import time
import re
from bs4 import BeautifulSoup
import os

def collect_cases(length:int=1000,article:str='6',start_year:int=1955,end_year:int=2026):
    """Queries the HUDOC rest api and collects the metadata storing it in a dataframe

    Args:
        length (int, optional): how many results to return per request. Defaults to 1000.
        article (str, optional): article of the ECHR you wish to retrieve for. Defaults to '6'.
        start_year (int, optional): year to start your query. Defaults to 1955.
        end_year (int, optional): year to end your query. Defaults to 2026.

    Returns:
        df (pd.DataFrame): dataframe containing the raw metadata
    """
    
    #fields the api is expected to return 
    fields = "sharepointid,rank,echrranking,applicability,languagenumber,itemid,judges,courts,originatingbody,docname,doctype,application,appno,sclappnos,conclusion,importance,originatingbody,typedescription,kpdate,kpdateastext,documentcollectionid,documentcollectionid2,languageisocode,extractedappno,isplaceholder,doctypebranch,respondent,advopidentifier,advopstatus,ecli,appnoparts,ECHRConcepts,article,violation,nonviolation,introductiondate,judgementdate,kpthesaurus,separateopinion,strasbourgcase"
    raw_jsons = []
    
    for year in range(start_year,end_year+1,2):
        print(f"Collecting for year range: {year}-{year+2}")
        start = 0
        while True:
            #due to the hard limit of 10000 imposed by api we collect over years instead so filter by kpdate
            kpdate_filter = f' AND ((kpdate>="{year}-01-01T00:00:00.0Z")) AND ((kpdate<"{year+2}-01-01T00:00:00.0Z"))'

            #for reproduciblillty I have changed the final date to reflect when the data was initially collected 16.02.26. Feel free to edit if you require more recent data. 
            if 2026 in range(year,year+2):
                kpdate_filter = f' AND ((kpdate>="{year}-01-01T00:00:00.0Z")) AND ((kpdate<"2026-02-16T00:00:00.0Z"))'
                
            url = (
                f'https://hudoc.echr.coe.int/app/query/results?query=contentsitename%3AECHR%20AND%20'
                f'(NOT%20(doctype%3DPR%20OR%20doctype%3DHFCOMOLD%20OR%20doctype%3DHECOMOLD))%20AND%20'
                f'((article%3D{article})){kpdate_filter}%20AND%20'
                f'((documentcollectionid%3D%22JUDGMENTS%22)%20OR%20(documentcollectionid%3D%22DECISIONS%22))'
                f'&select={fields}&sort=&start={start}&length={length}&rankingModelId=11111111-0000-0000-0000-000000000000'
            )
            
            response = requests.get(url)
            data = response.json()
            results = data.get('results', [])
            
            #if results list is empty break loop
            if not results:
                break
            
            for entry in results:
                raw_jsons.append(entry['columns'])
                        
            #target as of 16.02.26 is ~82550 files
            print(f"Gathered {len(results)} records (total: {len(raw_jsons)})")
            
            start += length
            time.sleep(1)
    
    if raw_jsons:  
        #save as a raw json
        df = pd.DataFrame(raw_jsons)
        
        #uncomment if you want to save full unprocessed metadata
        #df.to_json("./data/hudoc_art6_raw_metadata.json", orient="records", lines=True)
        #print(f"Saved {len(df)} records to ./data/hudoc_art6_raw_metadata.json")
        
        return df
    
    else:
        print("No records found.")
    
def process_cases(data:pd.DataFrame):
    """sorts the cases into decisions/judgments and others
    for art.6 others are screening panel cases
    """
    #regex patterns
    is_judgment = re.compile(r'.*JUD', re.IGNORECASE)
    is_decision = re.compile(r'.*DEC', re.IGNORECASE)
    
    judgments = []
    decisions = []
    
    other = []

    for _, case in data.iterrows():
        
        doctype = case['doctype']

        if is_judgment.match(doctype):
            judgments.append(case)
        elif is_decision.match(doctype):
            decisions.append(case)
            
        #for Art.6 these are screening panel decisions (prior to its reppeal in 1998)
        else:
            other.append(case)
        
    print(f"Sorted: {len(judgments)} Judgments, {len(decisions)} Decisions, {len(other)} Screening Panel")

    return judgments, decisions, other

def appno_mapping(df:pd.DataFrame):
    """
    creates a mapping of all appnos to relevant ecli and itemids for later use
    """

    def clean_appnos(text):
        """
        clean the appnos column with different appnos linked to a judgment
        """
        cleaned = re.sub(r'[^0-9/ ;]', ';', text)
        cleaned = cleaned.replace(" ", ";")
        cleaned = re.sub(r';+', ';', cleaned).strip(';')
        return cleaned

    df['appno_clean'] = df['appno'].apply(clean_appnos)

    #create list of appnos
    df['appno_clean'] = df['appno_clean'].str.split(';')

    #explode list with row for each app no and link to ecli/itemid for easier tetx extraction
    mapping_df = df.explode('appno_clean')[['appno_clean', 'ecli', 'itemid']]
    mapping_df.columns = ['individual_appno', 'ecli', 'itemid']

    #remove empty results
    mapping_df = mapping_df[mapping_df['individual_appno'] != ""]

    #save mapping
    mapping_df.to_csv('./data/echr_appno_mapping.csv', index=False)

def sort_language(df: pd.DataFrame):

    print(f"Current size of dataset: {len(df)}")
    
    df = df[df['languageisocode'].isin(['ENG', 'FRE'])]
    
    print(f"Size of dataset after filtering to ENG/FRE languages: {len(df)}")
    
    #order by ECLI and language, ENG first
    df = df.sort_values(by=['languageisocode','ecli'])

    print(f"Size of dataset after sorting: {len(df)} with {(df['languageisocode'] == 'ENG').sum()} English cases and {(df['languageisocode'] == 'FRE').sum()} French cases.")
    
    return df

def retrieve_text(data):
    out_dir = "./data/case_text/"
    os.makedirs(out_dir, exist_ok=True)
    extracted_eclis = set()
    for _, row in data.iterrows():
        ecli = row['ecli']
        id = row['itemid']
        lang = row['languageisocode']
        out_path = os.path.join(out_dir, f"{id}.html")
        # Skip if already extracted for this ECLI
        if ecli in extracted_eclis:
            continue
        # Skip if file already exists
        if os.path.isfile(out_path):
            print(f"Already exists, skipping: {out_path}")
            extracted_eclis.add(ecli)
            continue
        url = "https://hudoc.echr.coe.int/app/conversion/docx/html/body?library=ECHR&id=" + str(id)
        try:
            page = requests.get(url, timeout=30)
            if page.status_code == 200:
                soup = BeautifulSoup(page.content, 'html.parser')
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(soup.prettify())
                print(f"Saved: {out_path} for ECLI {ecli} ({lang})")
                extracted_eclis.add(ecli)
            else:
                print(f"Failed to fetch {id}: HTTP {page.status_code}")
        except Exception as e:
            print(f"Error fetching {id}: {e}")
        time.sleep(0.5)
        
def check_retrieval():
    """checks all cases have text retrieved. if they have not then filters df to only those cases which have and exports it"""
    
    #print covergage of text 
    
    pass

if __name__ == '__main__':
    #recommended running of the functions within this script
    
    #raw_df = collect_cases()
    raw_df = pd.read_json("./data/hudoc_art6_raw_metadata.json",lines=True)
    judgments, _, _ = process_cases(raw_df)
    data = pd.DataFrame(judgments)
    
    #appno_mapping(data)
    
    data_no_dupe = sort_language(data)
    
    pd.set_option('display.max_columns', None)
    print(data_no_dupe.head(40))
    print(data_no_dupe.columns)
    
    #data_no_dupe.to_json("./data/hudoc_art_6_judgments_metadata.json", orient="records", lines=True)
    #print(f"Saved {len(data_no_dupe)} records to ./data/hudoc_art_6_judgments_metadata.json")
    
    #retrieve_text(data_no_dupe)





