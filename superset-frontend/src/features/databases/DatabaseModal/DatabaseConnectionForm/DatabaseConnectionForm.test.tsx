/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { fireEvent, render, screen } from 'spec/helpers/testing-library';
import {
  ConfigurationMethod,
  DatabaseConnectionFormProps,
  DatabaseForm,
  DatabaseObject,
  Engines,
} from 'src/features/databases/types';
import DatabaseConnectionForm, { computeInitialIsPublic } from './index';

const baseDb: Partial<DatabaseObject> = {
  configuration_method: ConfigurationMethod.DynamicForm,
  database_name: 'test',
  driver: 'apsw',
  id: 1,
  name: 'test',
  is_managed_externally: false,
  engine: Engines.GSheet,
};

test('computeInitialIsPublic: returns true when database is null/undefined', () => {
  expect(computeInitialIsPublic(null)).toBe(true);
  expect(computeInitialIsPublic(undefined)).toBe(true);
});

test('computeInitialIsPublic: returns true for non-gsheets engines', () => {
  expect(
    computeInitialIsPublic({ ...baseDb, engine: 'postgres' as string }),
  ).toBe(true);
});

test('computeInitialIsPublic: returns true for fresh gsheets connections', () => {
  expect(computeInitialIsPublic({ ...baseDb })).toBe(true);
  expect(
    computeInitialIsPublic({ ...baseDb, masked_encrypted_extra: '{}' }),
  ).toBe(true);
});

test('computeInitialIsPublic: returns false when masked_encrypted_extra has content', () => {
  expect(
    computeInitialIsPublic({
      ...baseDb,
      masked_encrypted_extra: JSON.stringify({
        service_account_info: { type: 'service_account' },
      }),
    }),
  ).toBe(false);
});

test('computeInitialIsPublic: returns false when parameters.service_account_info is set', () => {
  expect(
    computeInitialIsPublic({
      ...baseDb,
      parameters: { service_account_info: '{"key":"value"}' },
    }),
  ).toBe(false);
});

test('computeInitialIsPublic: returns false when parameters.oauth2_client_info is set (OAuth2-only edit)', () => {
  expect(
    computeInitialIsPublic({
      ...baseDb,
      parameters: {
        // oauth2_client_info isn't in DatabaseParameters typing yet; this
        // mirrors how an OAuth2-only edit-mode payload can arrive.
        oauth2_client_info: { id: 'client-id' },
      } as DatabaseObject['parameters'],
    }),
  ).toBe(false);
});

// Mirrors the JSON schema OracleEngineSpec.parameters_json_schema() returns:
// no plain `database` field, but explicit `service_name` and `sid`.
const oracleDbModel = {
  parameters: {
    properties: {
      host: { description: 'Hostname or IP address' },
      port: { description: 'Database port' },
      service_name: { description: 'Oracle service name' },
      sid: { description: 'Oracle SID' },
      username: { description: 'Username' },
      password: { description: 'Password' },
    },
    required: ['host', 'port', 'username'],
  },
} as unknown as DatabaseForm;

const noop = () => {};

const renderOracleForm = (
  overrides: Partial<DatabaseConnectionFormProps> = {},
) =>
  render(
    <DatabaseConnectionForm
      dbModel={oracleDbModel}
      db={{ parameters: {} } as Partial<DatabaseObject>}
      sslForced={false}
      isValidating={false}
      onParametersChange={noop}
      onChange={noop}
      onQueryChange={noop}
      onExtraInputChange={noop}
      onEncryptedExtraInputChange={noop}
      onClearEncryptedExtraKey={noop}
      onAddTableCatalog={noop}
      onRemoveTableCatalog={noop}
      validationErrors={null}
      getValidation={noop}
      clearValidationErrors={noop}
      {...overrides}
    />,
  );

test('Oracle form renders Service name and SID fields and no Database name field', () => {
  renderOracleForm();
  expect(screen.getByText('Service name')).toBeInTheDocument();
  expect(screen.getByText('SID')).toBeInTheDocument();
  expect(screen.getByText('Host')).toBeInTheDocument();
  expect(screen.getByText('Port')).toBeInTheDocument();
  // Oracle has no plain "Database name" field; it uses service_name / sid
  expect(screen.queryByText('Database name')).not.toBeInTheDocument();
});

test('Oracle service_name input writes back through onParametersChange', () => {
  const onParametersChange = jest.fn();
  renderOracleForm({ onParametersChange });
  const input = document.querySelector(
    'input[name="service_name"]',
  ) as HTMLInputElement;
  expect(input).toBeInTheDocument();
  fireEvent.change(input, { target: { value: 'ORCLPDB1' } });
  expect(onParametersChange).toHaveBeenCalled();
});
