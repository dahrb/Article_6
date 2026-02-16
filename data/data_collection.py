"""
Script to collect Art. 6 decisions and judgments through the HUDOC Rest API

Last Updated:
16.02.26

History:
v1_0 - retrieves and sorts the cases into judgments/decisions
"""

import pandas as pd
import requests
import time
import re

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
    
def process_cases(data):
    
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

if __name__ == '__main__':
    #recommended running of the functions within this script
    
    #raw_df = collect_cases()
    raw_df = pd.read_json("./data/hudoc_art6_raw_metadata.json",lines=True)
    judgments, _, _ = process_cases(raw_df)
    
    

