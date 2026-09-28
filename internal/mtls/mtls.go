// Package mtls builds mutual-TLS configurations for the collector -> central
// hop. Both ends load the same shape of material (a cert/key pair and a CA to
// verify the peer), so the loaders live here and neither package reimplements
// certificate parsing.
//
// Threat model note: the inventory and drift payloads name exactly which agent
// persona files exist and what changed in them. That is a map of your fleet's
// soft spots, so the hop is authenticated in both directions — a stolen
// collector cert still cannot impersonate central, and a rogue endpoint cannot
// enroll as a collector.
package mtls

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"os"
)

// Config is the file-path material for one side of an mTLS connection.
type Config struct {
	// CertFile/KeyFile are this side's certificate and private key.
	CertFile string `yaml:"cert" json:"cert"`
	KeyFile  string `yaml:"key" json:"key"`
	// CAFile verifies the peer's certificate. Required whenever TLS is enabled:
	// without it we would be encrypting to an unauthenticated stranger.
	CAFile string `yaml:"ca" json:"ca"`
	// ServerName overrides the name checked against the peer cert (defaults to
	// the URL host on the client, or the listener's expectation on the server).
	ServerName string `yaml:"server_name,omitempty" json:"server_name,omitempty"`
	// InsecureSkipVerify disables peer verification. It exists ONLY so tests can
	// use self-signed certs without a CA dance; production code must leave it
	// false. Named to be uncomfortable to set.
	InsecureSkipVerify bool `yaml:"insecure_skip_verify,omitempty" json:"insecure_skip_verify,omitempty"`
}

// Enabled reports whether TLS material was configured at all.
func (c Config) Enabled() bool {
	return c.CertFile != "" || c.KeyFile != "" || c.CAFile != ""
}

// Validate checks that the material is either absent or complete and coherent.
func (c Config) Validate() error {
	if !c.Enabled() {
		return nil
	}
	if c.CertFile == "" || c.KeyFile == "" {
		return errors.New("mtls: both cert and key are required when TLS is enabled")
	}
	if c.CAFile == "" && !c.InsecureSkipVerify {
		return errors.New("mtls: ca is required when TLS is enabled (or set insecure_skip_verify for tests)")
	}
	return nil
}

// loadKeyPair reads and parses this side's certificate/key.
func (c Config) loadKeyPair() (tls.Certificate, error) {
	cert, err := tls.LoadX509KeyPair(c.CertFile, c.KeyFile)
	if err != nil {
		return tls.Certificate{}, fmt.Errorf("mtls: load key pair: %w", err)
	}
	return cert, nil
}

// loadCAPool builds a certificate pool from CAFile. A nil pool with no CA is
// the signal to use the system pool.
func (c Config) loadCAPool() (*x509.CertPool, error) {
	if c.CAFile == "" {
		return nil, nil
	}
	raw, err := os.ReadFile(c.CAFile)
	if err != nil {
		return nil, fmt.Errorf("mtls: read ca: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(raw) {
		return nil, fmt.Errorf("mtls: %s contains no valid PEM certificates", c.CAFile)
	}
	return pool, nil
}

// ClientConfig returns a *tls.Config for the collector (client) side. It always
// presents a client certificate and verifies central's.
func (c Config) ClientConfig() (*tls.Config, error) {
	if err := c.Validate(); err != nil {
		return nil, err
	}
	cert, err := c.loadKeyPair()
	if err != nil {
		return nil, err
	}
	pool, err := c.loadCAPool()
	if err != nil {
		return nil, err
	}
	return &tls.Config{
		Certificates:       []tls.Certificate{cert},
		RootCAs:            pool, // nil -> system roots
		ServerName:         c.ServerName,
		InsecureSkipVerify: c.InsecureSkipVerify, // #nosec G402 -- gated by config, test-only
		MinVersion:         tls.VersionTLS12,
	}, nil
}

// ServerConfig returns a *tls.Config for the central (server) side. It requires
// and verifies a client certificate (ClientAuth: RequireAndVerifyClientCert) —
// an unauthenticated collector is rejected at the handshake, before any body is
// read.
func (c Config) ServerConfig() (*tls.Config, error) {
	if err := c.Validate(); err != nil {
		return nil, err
	}
	cert, err := c.loadKeyPair()
	if err != nil {
		return nil, err
	}
	pool, err := c.loadCAPool()
	if err != nil {
		return nil, err
	}
	return &tls.Config{
		Certificates: []tls.Certificate{cert},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    pool,
		MinVersion:   tls.VersionTLS12,
	}, nil
}
