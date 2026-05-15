use openmls::group::{
    MlsGroupCreateConfig,
    MlsGroupJoinConfig,
    PURE_CIPHERTEXT_WIRE_FORMAT_POLICY,
};
use openmls::prelude::*;
use openmls_basic_credential::SignatureKeyPair;
use openmls_rust_crypto::{MemoryStorage, RustCrypto};
use openmls_traits::OpenMlsProvider;
use serde::{Deserialize, Serialize};
use tls_codec::{Deserialize as TlsDeserialize, Serialize as TlsSerialize};

pub type EnigmaResult<T> = Result<T, String>;

fn enigma_err<E: std::fmt::Display>(err: E) -> String {
    err.to_string()
}

#[derive(Debug)]
pub struct EnigmaProvider {
    crypto: RustCrypto,
    storage: MemoryStorage,
}

impl Default for EnigmaProvider {
    fn default() -> Self {
        Self {
            crypto: RustCrypto::default(),
            storage: MemoryStorage::default(),
        }
    }
}

impl OpenMlsProvider for EnigmaProvider {
    type CryptoProvider = RustCrypto;
    type RandProvider = RustCrypto;
    type StorageProvider = MemoryStorage;

    fn storage(&self) -> &Self::StorageProvider {
        &self.storage
    }

    fn crypto(&self) -> &Self::CryptoProvider {
        &self.crypto
    }

    fn rand(&self) -> &Self::CryptoProvider {
        &self.crypto
    }
}

#[derive(Debug, Serialize, Deserialize)]
struct ExportedState {
    room_slug: String,
    identity: String,
    storage: Vec<u8>,
    signature_keys: Option<Vec<u8>>,
    group_id: Option<Vec<u8>>,
}

pub struct EnigmaSession {
    room_slug: String,
    identity: String,
    provider: EnigmaProvider,
    signature_keys: Option<SignatureKeyPair>,
    group_id: Option<GroupId>,
}

impl EnigmaSession {
    pub fn new(room_slug: String, identity: String) -> EnigmaResult<Self> {
        Ok(Self {
            room_slug,
            identity,
            provider: EnigmaProvider::default(),
            signature_keys: None,
            group_id: None,
        })
    }

    pub fn ciphersuite() -> Ciphersuite {
        Ciphersuite::MLS_128_DHKEMX25519_AES128GCM_SHA256_Ed25519
    }

    fn fallback_group_id(&self) -> GroupId {
        GroupId::from_slice(self.room_slug.as_bytes())
    }

    fn active_group_id(&self) -> GroupId {
        self.group_id
            .clone()
            .unwrap_or_else(|| self.fallback_group_id())
    }

    fn credential_with_key(&self, signature_keys: &SignatureKeyPair) -> CredentialWithKey {
        let credential = BasicCredential::new(self.identity.as_bytes().to_vec());

        CredentialWithKey {
            credential: credential.into(),
            signature_key: signature_keys.to_public_vec().into(),
        }
    }

    fn create_config() -> MlsGroupCreateConfig {
        MlsGroupCreateConfig::builder()
            .wire_format_policy(PURE_CIPHERTEXT_WIRE_FORMAT_POLICY)
            .ciphersuite(Self::ciphersuite())
            .use_ratchet_tree_extension(true)
            .build()
    }

    fn join_config() -> MlsGroupJoinConfig {
        MlsGroupJoinConfig::builder()
            .wire_format_policy(PURE_CIPHERTEXT_WIRE_FORMAT_POLICY)
            .use_ratchet_tree_extension(true)
            .build()
    }

    fn ensure_signature_keys(&mut self) -> EnigmaResult<()> {
        if self.signature_keys.is_some() {
            return Ok(());
        }

        let signature_keys =
            SignatureKeyPair::new(Self::ciphersuite().into()).map_err(enigma_err)?;

        signature_keys
            .store(self.provider.storage())
            .map_err(enigma_err)?;

        self.signature_keys = Some(signature_keys);

        Ok(())
    }

    fn load_group(&self) -> EnigmaResult<MlsGroup> {
        let group_id = self.active_group_id();

        MlsGroup::load(self.provider.storage(), &group_id)
            .map_err(enigma_err)?
            .ok_or_else(|| {
                format!(
                    "session has not joined MLS group {} yet",
                    self.room_slug
                )
            })
    }

    pub fn create_group(&mut self) -> EnigmaResult<()> {
        self.ensure_signature_keys()?;

        let signature_keys = self
            .signature_keys
            .as_ref()
            .ok_or_else(|| "signing keys are missing after ensure_signature_keys()".to_string())?;

        let credential_with_key = self.credential_with_key(signature_keys);

        let group = MlsGroup::new(
            &self.provider,
            signature_keys,
            &Self::create_config(),
            credential_with_key,
        )
        .map_err(enigma_err)?;

        self.group_id = Some(group.group_id().clone());

        Ok(())
    }

    pub fn add_member(&mut self, key_package_bytes: &[u8]) -> EnigmaResult<(Vec<u8>, Vec<u8>)> {
        let mut group = self.load_group()?;

        let signature_keys = self
            .signature_keys
            .as_ref()
            .ok_or_else(|| {
                "signing keys are missing; call create_group() before add_member()".to_string()
            })?;

        let key_package_message =
            MlsMessageIn::tls_deserialize_exact(key_package_bytes).map_err(enigma_err)?;

        let key_package_in = match key_package_message.extract() {
            MlsMessageBodyIn::KeyPackage(key_package) => key_package,
            _ => return Err("expected an MLS KeyPackage message".to_string()),
        };

        let key_package = key_package_in
            .validate(self.provider.crypto(), ProtocolVersion::Mls10)
            .map_err(enigma_err)?;

        let key_packages = vec![key_package];

        let (commit, welcome, _group_info) = group
            .add_members(&self.provider, signature_keys, &key_packages)
            .map_err(enigma_err)?;

        group
            .merge_pending_commit(&self.provider)
            .map_err(enigma_err)?;

        self.group_id = Some(group.group_id().clone());

        let welcome_bytes = MlsMessageOut::from(welcome)
            .to_bytes()
            .map_err(enigma_err)?;

        let commit_bytes = commit.to_bytes().map_err(enigma_err)?;

        Ok((welcome_bytes, commit_bytes))
    }

    pub fn key_package(&mut self) -> EnigmaResult<Vec<u8>> {
        let ciphersuite = Self::ciphersuite();

        self.ensure_signature_keys()?;

        let signature_keys = self
            .signature_keys
            .as_ref()
            .ok_or_else(|| "signing keys are missing after ensure_signature_keys()".to_string())?;

        let credential_with_key = self.credential_with_key(signature_keys);

        let key_package_bundle = KeyPackage::builder()
            .build(
                ciphersuite,
                &self.provider,
                signature_keys,
                credential_with_key,
            )
            .map_err(enigma_err)?;

        let key_package = key_package_bundle.key_package().clone();

        let message_out = MlsMessageOut::from(key_package);
        let bytes = message_out.to_bytes().map_err(enigma_err)?;

        Ok(bytes)
    }

    pub fn join_from_welcome(&mut self, welcome_bytes: &[u8]) -> EnigmaResult<()> {
        let message = MlsMessageIn::tls_deserialize_exact(welcome_bytes).map_err(enigma_err)?;

        let welcome = match message.extract() {
            MlsMessageBodyIn::Welcome(welcome) => welcome,
            _ => return Err("expected an MLS Welcome message".to_string()),
        };

        let staged_welcome =
            StagedWelcome::new_from_welcome(&self.provider, &Self::join_config(), welcome, None)
                .map_err(enigma_err)?;

        let group = staged_welcome
            .into_group(&self.provider)
            .map_err(enigma_err)?;

        self.group_id = Some(group.group_id().clone());

        Ok(())
    }

    pub fn process_message(&mut self, message_bytes: &[u8]) -> EnigmaResult<Option<Vec<u8>>> {
        let mut group = self.load_group()?;

        let message = MlsMessageIn::tls_deserialize_exact(message_bytes).map_err(enigma_err)?;

        let protocol_message: ProtocolMessage = message
            .try_into_protocol_message()
            .map_err(|_| "expected an MLS PublicMessage or PrivateMessage".to_string())?;

        let processed_message = group
            .process_message(&self.provider, protocol_message)
            .map_err(enigma_err)?;

        match processed_message.into_content() {
            ProcessedMessageContent::ApplicationMessage(application_message) => {
                Ok(Some(application_message.into_bytes()))
            }

            ProcessedMessageContent::ProposalMessage(proposal)
            | ProcessedMessageContent::ExternalJoinProposalMessage(proposal) => {
                group
                    .store_pending_proposal(self.provider.storage(), *proposal)
                    .map_err(enigma_err)?;

                Ok(None)
            }

            ProcessedMessageContent::StagedCommitMessage(staged_commit) => {
                group
                    .merge_staged_commit(&self.provider, *staged_commit)
                    .map_err(enigma_err)?;

                self.group_id = Some(group.group_id().clone());

                Ok(None)
            }
        }
    }

    pub fn encrypt_app(&mut self, plaintext: &[u8]) -> EnigmaResult<Vec<u8>> {
        let mut group = self.load_group()?;

        let signature_keys = self
            .signature_keys
            .as_ref()
            .ok_or_else(|| {
                "signing keys are missing; create a key package or group first".to_string()
            })?;

        let message_out = group
            .create_message(&self.provider, signature_keys, plaintext)
            .map_err(enigma_err)?;

        let bytes = message_out.to_bytes().map_err(enigma_err)?;

        Ok(bytes)
    }

    pub fn export_state(&self) -> EnigmaResult<Vec<u8>> {
        let mut storage = Vec::new();

        self.provider
            .storage
            .serialize(&mut storage)
            .map_err(enigma_err)?;

        let signature_keys = self
            .signature_keys
            .as_ref()
            .map(|keys| keys.tls_serialize_detached().map_err(enigma_err))
            .transpose()?;

        let group_id = self
            .group_id
            .as_ref()
            .map(|id| id.tls_serialize_detached().map_err(enigma_err))
            .transpose()?;

        let bytes = serde_json::to_vec(&ExportedState {
            room_slug: self.room_slug.clone(),
            identity: self.identity.clone(),
            storage,
            signature_keys,
            group_id,
        })
        .map_err(enigma_err)?;

        Ok(bytes)
    }

    pub fn import_state(state_bytes: &[u8]) -> EnigmaResult<Self> {
        let state: ExportedState = serde_json::from_slice(state_bytes).map_err(enigma_err)?;

        let storage = if state.storage.is_empty() {
            MemoryStorage::default()
        } else {
            MemoryStorage::deserialize(&mut state.storage.as_slice()).map_err(enigma_err)?
        };

        let signature_keys = state
            .signature_keys
            .as_deref()
            .map(SignatureKeyPair::tls_deserialize_exact)
            .transpose()
            .map_err(enigma_err)?;

        let group_id = state
            .group_id
            .as_deref()
            .map(GroupId::tls_deserialize_exact)
            .transpose()
            .map_err(enigma_err)?;

        Ok(Self {
            room_slug: state.room_slug,
            identity: state.identity,
            provider: EnigmaProvider {
                crypto: RustCrypto::default(),
                storage,
            },
            signature_keys,
            group_id,
        })
    }
}