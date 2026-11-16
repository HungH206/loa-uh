"""User-scoped Supabase REST access. Never uses a service-role key."""
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException

COLUMNS = 'id,occurred_at,created_at,location,description,feeling,details'


def iso_time(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def api_report(row):
    row = dict(row)
    for name in ('occurred_at', 'created_at'):
        row[name] = datetime.fromisoformat(row[name].replace('Z', '+00:00')).timestamp()
    return {key: row[key] for key in COLUMNS.split(',')}


class SupabaseReports:
    def __init__(self, url, key, token, user):
        self.url = url.rstrip('/') + '/rest/v1/incident_reports'
        self.headers = {'apikey': key, 'Authorization': f'Bearer {token}'}
        self.user = user

    async def request(self, method, params=None, rows=None):
        headers = dict(self.headers)
        if method == 'POST':
            headers['Prefer'] = 'resolution=ignore-duplicates,return=minimal'
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.request(method, self.url, params=params, json=rows, headers=headers)
        except httpx.RequestError:
            raise HTTPException(503, 'Cloud report storage is unavailable. Your report was not confirmed saved; please retry.')
        if response.status_code == 401:
            raise HTTPException(401, 'Your session has expired. Sign in again.')
        if response.status_code == 403:
            raise HTTPException(403, 'Cloud report access was denied. Check the incident_reports access policies.')
        if not response.is_success:
            raise HTTPException(503, 'Cloud reports are unavailable. Check the incident_reports migration and Supabase connection.')
        return response.json() if method == 'GET' else None

    async def insert(self, rows):
        # Force ownership from the verified session, including for legacy migration.
        values = [{**row, 'user_id': self.user} for row in rows]
        await self.request('POST', {'on_conflict':'user_id,request_id'}, values)

    async def list(self, request_id=None):
        params = {'select':COLUMNS,'user_id':f'eq.{self.user}',
                  'order':'occurred_at.desc,id.desc','limit':'50'}
        if request_id:
            params['request_id'] = f'eq.{request_id}'
        return [api_report(row) for row in await self.request('GET', params)]
