import assert from 'node:assert/strict';
import {test} from 'node:test';
import {identityLabel,describeError} from './ConnectionPreflight.tsx';
test('unknown and verified identity labels remain separate from send permission',()=>{
 assert.equal(identityLabel('unknown'),'identity unknown');
 assert.equal(identityLabel('identity_verified'),'identity verified');
 assert.equal(identityLabel('succeeded'),'unverified');
 assert.equal(identityLabel('identity_mismatch'),'identity mismatch');
});
test('Claude pasted code masking is preserved',()=>{
 assert.equal(describeError('failed DUMMY-CODE','DUMMY-CODE'),'failed [code]');
});
