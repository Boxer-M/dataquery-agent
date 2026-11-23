"""Explicitly replicate already approved PG vectors; never generate embeddings."""
import argparse
import json
import os
from secrets import token_hex
from dotenv import dotenv_values

from dataquery_agent.metrics import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('init-config','sync'))
    parser.add_argument('--document-id',action='append')
    args=parser.parse_args()
    if args.command=='init-config':
        with os.fdopen(os.open(ROOT/'.env.milvus',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as out:
            out.write('MILVUS_PASSWORD='+token_hex(32)+'\nMILVUS_URL=http://127.0.0.1:19530\nRAG_VECTOR_BACKEND=pgvector\n')
        print('Created ignored 600 config; password not printed')
        return
    from dataquery_agent.rag.embedding import GeminiEmbedding
    from dataquery_agent.rag.milvus import sync_visible
    from dataquery_agent.rag.store import KnowledgeStore
    owner=dotenv_values(ROOT/'.env.agent-meta').get('AGENT_OWNER_TOKEN')
    result=sync_visible(KnowledgeStore.local(),GeminiEmbedding(None),owner=owner,document_ids=args.document_id)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
