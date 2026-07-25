# External database certificates

Place CA or client certificates required by an external PostgreSQL service in this directory.
The API mounts it read-only at `/app/certs`. Do not commit private keys or production certificates.
