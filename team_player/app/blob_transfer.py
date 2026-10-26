"""Upload/download one explicit tree using Entra credentials, never account keys."""
import argparse
from pathlib import Path, PurePosixPath

def safe_destination(root, name):
    relative=PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name:
        raise ValueError('Unsafe blob path')
    path=(root/str(relative)).resolve()
    if root.resolve() not in path.parents: raise ValueError('Blob escapes output directory')
    return path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('direction',choices=['upload','download'])
    p.add_argument('--account-url',required=True)
    p.add_argument('--container',required=True)
    p.add_argument('--prefix',required=True,help='Unique run ID, e.g. run-0042/')
    p.add_argument('--directory',required=True)
    args=p.parse_args()
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
    import os
    credential=DefaultAzureCredential(managed_identity_client_id=os.environ.get('AZURE_CLIENT_ID'))
    root=Path(args.directory).resolve();prefix=args.prefix.strip('/')+'/'
    with credential, BlobServiceClient(args.account_url,credential=credential) as service:
        container=service.get_container_client(args.container)
        if args.direction=='upload':
            if not root.is_dir(): raise ValueError('Upload directory does not exist')
            for path in sorted(root.rglob('*')):
                if path.is_symlink(): raise ValueError('Symlinks are not upload inputs')
                if path.is_file():
                    with path.open('rb') as handle:
                        container.upload_blob(prefix+path.relative_to(root).as_posix(),handle,overwrite=False)
        else:
            root.mkdir(parents=True,exist_ok=False)
            for blob in container.list_blobs(name_starts_with=prefix):
                path=safe_destination(root,blob.name[len(prefix):])
                path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as handle: container.download_blob(blob.name).readinto(handle)

if __name__=='__main__': main()
