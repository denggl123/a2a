"""Sign an artifact with an encrypted, persistent project publisher identity."""
import argparse
import json
from pathlib import Path
from a2n_node.release_signing import ReleasePublisher

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('artifact');parser.add_argument('--sequence',type=int,required=True)
    parser.add_argument('--publisher-home',default=str(Path(__file__).resolve().parents[1]/'data/release-publisher'))
    parser.add_argument('--platform',default='Windows');args=parser.parse_args()
    result=ReleasePublisher(args.publisher_home).sign(args.artifact,sequence=args.sequence,platform=args.platform)
    print(json.dumps({'author_did':result['author_did'],'sha256':result['sha256'],'signature_verified':True}))

if __name__=='__main__':main()
