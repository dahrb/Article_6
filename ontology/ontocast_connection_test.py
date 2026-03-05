from ontocast.tool.triple_manager.fuseki import FusekiTripleStoreManager

# Config
FUSEKI_BASE_URI = "http://localhost:3032"
DATASET_NAME = "ds"
AUTH = ("admin", "test345")

def run_test():
    print(f"Connecting to {FUSEKI_BASE_URI}...")
    
    try:
        # We DO NOT use 'await' or 'async def' here.
        # The library's __init__ will call its own asyncio.run() internally.
        manager = FusekiTripleStoreManager(
            uri=FUSEKI_BASE_URI,
            dataset=DATASET_NAME,
            auth=AUTH
        )
        
        print(f"✅ SUCCESS: Manager initialized for dataset '{DATASET_NAME}'!")
        print("Since curl worked, your tunnel is ready for the Article 6 run.")
        
    except Exception as e:
        print(f"❌ Initialization failed: {e}")

if __name__ == "__main__":
    run_test()