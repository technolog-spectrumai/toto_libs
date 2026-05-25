use rotor_core::RotorSession;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn py_err<E: std::fmt::Display>(err: E) -> PyErr {
    PyRuntimeError::new_err(err.to_string())
}

#[pyclass(name = "RotorEngine")]
pub struct PyRotorEngine;

#[pymethods]
impl PyRotorEngine {
    #[staticmethod]
    pub fn create_group_state<'py>(
        py: Python<'py>,
        room_slug: String,
        identity: String,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let mut session = RotorSession::new(room_slug, identity).map_err(py_err)?;
        session.create_group().map_err(py_err)?;

        let state = session.export_state().map_err(py_err)?;
        Ok(PyBytes::new(py, &state))
    }

    #[staticmethod]
    pub fn create_empty_state<'py>(
        py: Python<'py>,
        room_slug: String,
        identity: String,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let session = RotorSession::new(room_slug, identity).map_err(py_err)?;

        let state = session.export_state().map_err(py_err)?;
        Ok(PyBytes::new(py, &state))
    }

    #[staticmethod]
    pub fn key_package_from_state<'py>(
        py: Python<'py>,
        state_bytes: &[u8],
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let mut session = RotorSession::import_state(state_bytes).map_err(py_err)?;

        let key_package = session.key_package().map_err(py_err)?;
        let new_state = session.export_state().map_err(py_err)?;

        Ok((
            PyBytes::new(py, &new_state),
            PyBytes::new(py, &key_package),
        ))
    }

    #[staticmethod]
    pub fn join_from_welcome_from_state<'py>(
        py: Python<'py>,
        state_bytes: &[u8],
        welcome_bytes: &[u8],
    ) -> PyResult<Bound<'py, PyBytes>> {
        let mut session = RotorSession::import_state(state_bytes).map_err(py_err)?;

        session.join_from_welcome(welcome_bytes).map_err(py_err)?;

        let new_state = session.export_state().map_err(py_err)?;
        Ok(PyBytes::new(py, &new_state))
    }

    #[staticmethod]
    pub fn add_member_from_state<'py>(
        py: Python<'py>,
        state_bytes: &[u8],
        key_package_bytes: &[u8],
    ) -> PyResult<(
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
        Bound<'py, PyBytes>,
    )> {
        let mut session = RotorSession::import_state(state_bytes).map_err(py_err)?;

        let (welcome, commit) = session.add_member(key_package_bytes).map_err(py_err)?;
        let new_state = session.export_state().map_err(py_err)?;

        Ok((
            PyBytes::new(py, &new_state),
            PyBytes::new(py, &welcome),
            PyBytes::new(py, &commit),
        ))
    }

    #[staticmethod]
    pub fn process_message_from_state<'py>(
        py: Python<'py>,
        state_bytes: &[u8],
        message_bytes: &[u8],
    ) -> PyResult<(Bound<'py, PyBytes>, Option<Bound<'py, PyBytes>>)> {
        let mut session = RotorSession::import_state(state_bytes).map_err(py_err)?;

        let plaintext = session.process_message(message_bytes).map_err(py_err)?;
        let new_state = session.export_state().map_err(py_err)?;

        Ok((
            PyBytes::new(py, &new_state),
            plaintext.map(|bytes| PyBytes::new(py, &bytes)),
        ))
    }

    #[staticmethod]
    pub fn encrypt_app_from_state<'py>(
        py: Python<'py>,
        state_bytes: &[u8],
        plaintext: &[u8],
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let mut session = RotorSession::import_state(state_bytes).map_err(py_err)?;

        let ciphertext = session.encrypt_app(plaintext).map_err(py_err)?;
        let new_state = session.export_state().map_err(py_err)?;

        Ok((
            PyBytes::new(py, &new_state),
            PyBytes::new(py, &ciphertext),
        ))
    }
}

#[pymodule]
fn rotor_py(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyRotorEngine>()?;
    Ok(())
}