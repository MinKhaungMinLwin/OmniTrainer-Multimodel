import {
  AgentEvent,
  AgentRunDetail,
  Approval,
  Appointment,
  AutomationRun,
  ConversationMessage,
  CustomerCreate,
  Job,
  JobCreate,
  OmniApiClient,
  Tenant,
  VoiceCall,
} from "@omni/contracts";
import { colors, radii, spacing } from "@omni/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Constants from "expo-constants";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextStyle,
  TextInput,
  View,
  ViewStyle,
} from "react-native";

import { sessionStore } from "../src/session";

const host = Constants.expoConfig?.hostUri?.split(":")[0] ?? "localhost";
const API_URL = process.env.EXPO_PUBLIC_API_URL ?? `http://${host}:8001`;

export default function HomeScreen() {
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([sessionStore.token(), sessionStore.tenantId()]).then(
      ([savedToken, savedTenant]) => {
        setToken(savedToken);
        setTenantId(savedTenant);
        setReady(true);
      },
    );
  }, []);
  const api = useMemo(
    () =>
      new OmniApiClient({
        baseUrl: API_URL,
        getToken: () => token,
        getTenantId: () => tenantId,
      }),
    [token, tenantId],
  );

  if (!ready) return <StateView label="Opening Omni Model…" />;
  if (!token)
    return (
      <SignIn
        api={api}
        onToken={async (value) => {
          await sessionStore.setToken(value);
          setToken(value);
        }}
      />
    );
  return (
    <CustomerWorkspace
      api={api}
      tenantId={tenantId}
      onTenant={async (value) => {
        await sessionStore.setTenantId(value);
        setTenantId(value);
      }}
      onSignOut={async () => {
        await Promise.all([
          sessionStore.setToken(null),
          sessionStore.setTenantId(null),
        ]);
        setToken(null);
        setTenantId(null);
      }}
    />
  );
}

function SignIn({
  api,
  onToken,
}: {
  api: OmniApiClient;
  onToken: (token: string) => Promise<void>;
}) {
  const [email, setEmail] = useState("owner@omni.example");
  const signIn = useMutation({
    mutationFn: () => api.createDevelopmentToken(email),
    onSuccess: onToken,
  });
  return (
    <SafeAreaView style={styles.authPage}>
      <View style={styles.authCard}>
        <Text style={styles.eyebrow}>OMNI MODEL</Text>
        <Text style={styles.hero}>
          Your operation, wherever the work happens.
        </Text>
        <Text style={styles.body}>
          Customers and schedules stay connected from desk to field.
        </Text>
        <TextInput
          accessibilityLabel="Development account"
          autoCapitalize="none"
          keyboardType="email-address"
          onChangeText={setEmail}
          style={styles.input}
          value={email}
        />
        {signIn.error && (
          <Text style={styles.error}>{signIn.error.message}</Text>
        )}
        <PrimaryButton
          disabled={signIn.isPending}
          label={signIn.isPending ? "Signing in…" : "Continue"}
          onPress={() => signIn.mutate()}
        />
      </View>
    </SafeAreaView>
  );
}

function CustomerWorkspace({
  api,
  tenantId,
  onTenant,
  onSignOut,
}: {
  api: OmniApiClient;
  tenantId: string | null;
  onTenant: (tenant: string) => Promise<void>;
  onSignOut: () => Promise<void>;
}) {
  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api.tenants(),
  });
  useEffect(() => {
    const first = tenants.data?.[0];
    if (!tenantId && first) void onTenant(first.id);
  }, [tenantId, tenants.data, onTenant]);
  if (tenants.isPending) return <StateView label="Loading workspace…" />;
  if (tenants.isError) return <StateView label={tenants.error.message} />;
  if (!tenants.data.length) return <StateView label="No workspace assigned." />;
  const activeTenant =
    tenants.data.find((tenant: Tenant) => tenant.id === tenantId) ??
    tenants.data[0];
  return (
    <Customers
      api={api}
      tenant={activeTenant}
      tenants={tenants.data}
      onTenant={onTenant}
      onSignOut={onSignOut}
    />
  );
}

function Customers({
  api,
  tenant,
  tenants,
  onTenant,
  onSignOut,
}: {
  api: OmniApiClient;
  tenant: Tenant;
  tenants: Tenant[];
  onTenant: (tenant: string) => Promise<void>;
  onSignOut: () => Promise<void>;
}) {
  const cache = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(
    null,
  );
  const [section, setSection] = useState<
    | "customers"
    | "jobs"
    | "schedule"
    | "invoices"
    | "team"
    | "assistant"
    | "automations"
    | "voice"
    | "intelligence"
  >("customers");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const customers = useQuery({
    queryKey: ["customers", tenant.id],
    queryFn: () => api.customers(),
  });
  const create = useMutation({
    mutationFn: () =>
      api.createCustomer({
        name: name.trim(),
        email: email || null,
        phone: null,
        notes: null,
        external_ref: null,
      } as CustomerCreate),
    onSuccess: async () => {
      setName("");
      setEmail("");
      setAdding(false);
      await cache.invalidateQueries({ queryKey: ["customers", tenant.id] });
    },
  });
  return (
    <SafeAreaView style={styles.page}>
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>{tenant.name.toUpperCase()}</Text>
          <Text style={styles.title}>
            {section[0].toUpperCase() + section.slice(1)}
          </Text>
        </View>
        <Pressable onPress={() => void onSignOut()}>
          <Text style={styles.link}>Sign out</Text>
        </Pressable>
      </View>
      {section === "customers" && (
        <View style={styles.toolbar}>
          <Text style={styles.body}>Customer relationships in one place.</Text>
          <PrimaryButton
            label={adding ? "Cancel" : "+ Add"}
            onPress={() => setAdding(!adding)}
          />
        </View>
      )}
      {tenants.length > 1 && (
        <View style={styles.tenantSwitcher}>
          {tenants.map((item) => (
            <Pressable
              key={item.id}
              onPress={() => void onTenant(item.id)}
              style={[
                styles.tenantPill,
                item.id === tenant.id && styles.tenantPillActive,
              ]}
            >
              <Text style={styles.tenantText}>{item.name}</Text>
            </Pressable>
          ))}
        </View>
      )}
      <View style={styles.mobileNav}>
        {(
          [
            "customers",
            "jobs",
            "schedule",
            "invoices",
            "team",
            "assistant",
            "automations",
            "voice",
            "intelligence",
          ] as const
        ).map((item) => (
          <Pressable key={item} onPress={() => setSection(item)}>
            <Text
              style={section === item ? styles.activeNav : styles.inactiveNav}
            >
              {item[0].toUpperCase() + item.slice(1)}
            </Text>
          </Pressable>
        ))}
      </View>
      {section === "customers" && adding && (
        <View style={styles.form}>
          <TextInput
            accessibilityLabel="Customer name"
            placeholder="Customer name"
            style={styles.input}
            value={name}
            onChangeText={setName}
          />
          <TextInput
            accessibilityLabel="Customer email"
            autoCapitalize="none"
            keyboardType="email-address"
            placeholder="Email"
            style={styles.input}
            value={email}
            onChangeText={setEmail}
          />
          {create.error && (
            <Text style={styles.error}>{create.error.message}</Text>
          )}
          <PrimaryButton
            disabled={!name.trim() || create.isPending}
            label={create.isPending ? "Saving…" : "Save customer"}
            onPress={() => create.mutate()}
          />
        </View>
      )}
      {section === "customers" && customers.isPending && (
        <ActivityIndicator color={colors.primary} />
      )}
      {section === "customers" && customers.isError && (
        <Text style={styles.error}>{customers.error.message}</Text>
      )}
      {section === "customers" && customers.data?.total === 0 && (
        <View style={styles.empty}>
          <Text style={styles.emptyIcon}>◎</Text>
          <Text style={styles.subtitle}>No customers yet</Text>
          <Text style={styles.body}>Add the first customer to begin.</Text>
        </View>
      )}
      {section === "customers" && selectedCustomerId && (
        <MobileCustomerDetail
          api={api}
          customerId={selectedCustomerId}
          tenantId={tenant.id}
          onClose={() => setSelectedCustomerId(null)}
        />
      )}
      {section === "customers" && !selectedCustomerId && (
        <FlatList
          data={customers.data?.items ?? []}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.list}
          renderItem={({ item }) => (
            <Pressable
              style={styles.customer}
              onPress={() => setSelectedCustomerId(item.id)}
            >
              <View style={styles.avatar}>
                <Text style={styles.avatarText}>
                  {item.name.slice(0, 2).toUpperCase()}
                </Text>
              </View>
              <View>
                <Text style={styles.customerName}>{item.name}</Text>
                <Text style={styles.body}>
                  {item.email ?? item.phone ?? "No contact details"}
                </Text>
              </View>
            </Pressable>
          )}
        />
      )}
      {section === "assistant" && (
        <MobileAssistant api={api} tenantId={tenant.id} />
      )}
      {section === "automations" && (
        <MobileAutomations api={api} tenantId={tenant.id} />
      )}
      {section === "voice" && <MobileVoice api={api} tenantId={tenant.id} />}
      {section === "intelligence" && (
        <MobileIntelligence api={api} tenantId={tenant.id} />
      )}
      {section !== "customers" &&
        section !== "assistant" &&
        section !== "automations" &&
        section !== "voice" &&
        section !== "intelligence" && (
          <MobileOperations
            api={api}
            tenantId={tenant.id}
            section={section}
            customers={customers.data?.items ?? []}
          />
        )}
    </SafeAreaView>
  );
}

function MobileVoice({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [callerName, setCallerName] = useState("Mobile pilot caller");
  const [reason, setReason] = useState("I need a service callback");
  const config = useQuery({
    queryKey: ["voice-config", tenantId],
    queryFn: () => api.voiceConfig(),
  });
  const calls = useQuery({
    queryKey: ["voice-calls", tenantId],
    queryFn: () => api.voiceCalls(),
    refetchInterval: 10_000,
  });
  const detail = useQuery({
    queryKey: ["voice-call", tenantId, selectedId],
    queryFn: () => api.voiceCall(selectedId!),
    enabled: Boolean(selectedId),
  });
  const refresh = async (call?: VoiceCall) => {
    if (call) setSelectedId(call.id);
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["voice-calls", tenantId] }),
      cache.invalidateQueries({
        queryKey: ["voice-call", tenantId, call?.id ?? selectedId],
      }),
      cache.invalidateQueries({ queryKey: ["voice-metrics", tenantId] }),
    ]);
  };
  const toggle = useMutation({
    mutationFn: () =>
      api.updateVoiceConfig({
        ...config.data!,
        enabled: !config.data!.enabled,
      }),
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: ["voice-config", tenantId] }),
  });
  const simulate = useMutation({
    mutationFn: () =>
      api.simulateVoiceCall({
        caller: "+15550100",
        callee: "+15550999",
        region: "local",
        turns: [
          {
            type: "transcript",
            text: "yes",
            confidence_bps: 9900,
            duration_ms: 300,
            heard_response_boundary_ms: 0,
          },
          {
            type: "transcript",
            text: callerName,
            confidence_bps: 9900,
            duration_ms: 600,
            heard_response_boundary_ms: 0,
          },
          {
            type: "transcript",
            text: reason,
            confidence_bps: 9900,
            duration_ms: 900,
            heard_response_boundary_ms: 0,
          },
          {
            type: "transcript",
            text: "yes",
            confidence_bps: 9900,
            duration_ms: 300,
            heard_response_boundary_ms: 0,
          },
          {
            type: "hangup",
            confidence_bps: 9900,
            duration_ms: 0,
            heard_response_boundary_ms: 0,
          },
        ],
      }),
    onSuccess: refresh,
  });
  const review = useMutation({
    mutationFn: ({
      call,
      decision,
    }: {
      call: VoiceCall;
      decision: "approve" | "reject";
    }) =>
      api.reviewVoiceCall(call.id, {
        decision,
        args: decision === "approve" ? call.review?.proposed_args : null,
        reason: `${decision}d on mobile`,
      }),
    onSuccess: refresh,
  });
  const transfer = useMutation({
    mutationFn: (call: VoiceCall) =>
      api.transferVoiceCall(call.id, "Requested by mobile operator"),
    onSuccess: refresh,
  });
  const active = detail.data;
  return (
    <ScrollView contentContainerStyle={styles.voiceMobile}>
      <View style={styles.mobileToolCard}>
        <Text style={styles.eyebrow}>INTERNAL PILOT</Text>
        <Text style={styles.subtitle}>After-hours intake</Text>
        <Text style={styles.body}>
          AI and recording consent are required. Emergency language and unsafe
          states transfer to a human.
        </Text>
        <PrimaryButton
          disabled={!config.data || toggle.isPending}
          label={config.data?.enabled ? "Disable pilot" : "Enable pilot"}
          onPress={() => toggle.mutate()}
        />
      </View>
      <View style={styles.form}>
        <Text style={styles.subtitle}>Safe call simulator</Text>
        <TextInput
          accessibilityLabel="Caller name"
          onChangeText={setCallerName}
          style={styles.input}
          value={callerName}
        />
        <TextInput
          accessibilityLabel="Callback reason"
          multiline
          onChangeText={setReason}
          style={styles.input}
          value={reason}
        />
        <PrimaryButton
          disabled={
            !config.data?.enabled ||
            !callerName.trim() ||
            !reason.trim() ||
            simulate.isPending
          }
          label={simulate.isPending ? "Running call…" : "Simulate call"}
          onPress={() => simulate.mutate()}
        />
      </View>
      <Text style={styles.subtitle}>Recent calls</Text>
      {calls.data?.map((call) => (
        <Pressable
          key={call.id}
          onPress={() => setSelectedId(call.id)}
          style={styles.mobileToolCard}
        >
          <Text style={styles.customerName}>{call.caller}</Text>
          <Text style={styles.fieldLabel}>
            {call.status} · {call.outcome ?? "in progress"}
          </Text>
        </Pressable>
      ))}
      {active && (
        <View style={styles.mobileToolCard}>
          <Text style={styles.subtitle}>Call review</Text>
          <Text style={styles.body}>
            Consent: {active.consent_status} · Recording:{" "}
            {active.recording_status}
          </Text>
          {active.transcript?.map((segment) => (
            <Text key={segment.id} style={styles.body}>
              {segment.speaker.toUpperCase()}: {segment.text}
            </Text>
          ))}
          {active.review?.status === "pending" && (
            <View style={styles.approvalCard}>
              <Text style={styles.customerName}>
                Callback requires approval
              </Text>
              <PrimaryButton
                label="Approve callback"
                onPress={() =>
                  review.mutate({ call: active, decision: "approve" })
                }
              />
              <Pressable
                onPress={() =>
                  review.mutate({ call: active, decision: "reject" })
                }
              >
                <Text style={styles.error}>Reject</Text>
              </Pressable>
            </View>
          )}
          {!(["completed", "failed", "transferred"] as string[]).includes(
            active.status,
          ) && (
            <Pressable onPress={() => transfer.mutate(active)}>
              <Text style={styles.link}>Transfer to human</Text>
            </Pressable>
          )}
        </View>
      )}
    </ScrollView>
  );
}

function MobileIntelligence({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const dashboard = useQuery({
    queryKey: ["intelligence-dashboard", tenantId],
    queryFn: () => api.intelligenceDashboard(),
  });
  const results = useQuery({
    queryKey: ["intelligence-calls", tenantId, query],
    queryFn: () => api.intelligenceCalls({ q: query || undefined }),
  });
  const voiceCalls = useQuery({
    queryKey: ["voice-calls", tenantId],
    queryFn: () => api.voiceCalls(),
  });
  const detail = useQuery({
    queryKey: ["intelligence-call", tenantId, selectedId],
    queryFn: () => api.intelligenceCall(selectedId!),
    enabled: Boolean(selectedId),
  });
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({
        queryKey: ["intelligence-dashboard", tenantId],
      }),
      cache.invalidateQueries({
        queryKey: ["intelligence-calls", tenantId],
      }),
      cache.invalidateQueries({
        queryKey: ["intelligence-call", tenantId],
      }),
    ]);
  };
  const processRecent = useMutation({
    mutationFn: async () => {
      const completed =
        voiceCalls.data?.filter((call) => call.ended_at).slice(0, 50) ?? [];
      await Promise.all(
        completed.map((call) => api.processIntelligenceCall(call.id)),
      );
    },
    onSuccess: refresh,
  });
  const review = useMutation({
    mutationFn: ({
      callId,
      decision,
    }: {
      callId: string;
      decision: "accept" | "reject";
    }) =>
      api.reviewIntelligence(callId, {
        decision,
        corrected_fields: null,
        reason: `${decision}ed on mobile`,
      }),
    onSuccess: refresh,
  });
  const metrics = dashboard.data;
  const active = detail.data;
  return (
    <ScrollView contentContainerStyle={styles.intelligenceMobile}>
      <View style={styles.mobileToolCard}>
        <Text style={styles.eyebrow}>GOVERNED CALL MART</Text>
        <Text style={styles.subtitle}>Conversation intelligence</Text>
        <Text style={styles.body}>
          {metrics?.total_calls ?? 0} modeled ·{" "}
          {Math.round((metrics?.containment_rate ?? 0) * 100)}% contained ·{" "}
          {Math.round((metrics?.reconciliation_rate ?? 0) * 100)}% reconciled
        </Text>
        <Text style={styles.body}>
          {metrics?.review_pending ?? 0} awaiting review ·{" "}
          {metrics?.compliance_flagged ?? 0} compliance flagged
        </Text>
        <PrimaryButton
          disabled={processRecent.isPending || voiceCalls.isPending}
          label={
            processRecent.isPending ? "Processing…" : "Process recent calls"
          }
          onPress={() => processRecent.mutate()}
        />
      </View>
      <TextInput
        accessibilityLabel="Search call intelligence"
        onChangeText={setQuery}
        placeholder="Search redacted calls"
        style={styles.input}
        value={query}
      />
      {results.data?.map((item) => (
        <Pressable
          key={item.call_id}
          onPress={() => setSelectedId(item.call_id)}
          style={styles.mobileToolCard}
        >
          <Text style={styles.customerName}>
            {item.topic.replaceAll("_", " ")}
          </Text>
          <Text style={styles.body}>{item.summary}</Text>
          <Text style={styles.fieldLabel}>
            {item.status} · {item.outcome} ·{" "}
            {Math.round(item.confidence_bps / 100)}%
          </Text>
        </Pressable>
      ))}
      {active && (
        <View style={styles.mobileToolCard}>
          <Text style={styles.subtitle}>Evidence and lineage</Text>
          <Text style={styles.body}>{active.intelligence.summary}</Text>
          <Text style={styles.fieldLabel}>
            transcript v{active.transcript.version} ·{" "}
            {active.pipeline.pipeline_version}
          </Text>
          {active.transcript.segments.map((segment) => (
            <Text
              key={`${String((segment as { sequence?: unknown }).sequence)}-${String((segment as { start_ms?: unknown }).start_ms)}`}
              style={styles.body}
            >
              {String((segment as { speaker?: unknown }).speaker).toUpperCase()}
              : {String((segment as { text?: unknown }).text)}
            </Text>
          ))}
          {active.intelligence.status === "pending_review" && (
            <View style={styles.approvalCard}>
              <Text style={styles.customerName}>Quality review required</Text>
              <PrimaryButton
                label="Accept extraction"
                onPress={() =>
                  review.mutate({
                    callId: active.intelligence.call_id,
                    decision: "accept",
                  })
                }
              />
              <Pressable
                onPress={() =>
                  review.mutate({
                    callId: active.intelligence.call_id,
                    decision: "reject",
                  })
                }
              >
                <Text style={styles.error}>Reject</Text>
              </Pressable>
            </View>
          )}
        </View>
      )}
    </ScrollView>
  );
}

function MobileCustomerDetail({
  api,
  customerId,
  tenantId,
  onClose,
}: {
  api: OmniApiClient;
  customerId: string;
  tenantId: string;
  onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: ["customer", tenantId, customerId],
    queryFn: () => api.customer(customerId),
  });
  if (detail.isPending) return <ActivityIndicator color={colors.primary} />;
  if (detail.isError)
    return <Text style={styles.error}>{detail.error.message}</Text>;
  return (
    <MobileCustomerDetailLoaded
      api={api}
      tenantId={tenantId}
      customer={detail.data}
      onClose={onClose}
    />
  );
}

function MobileCustomerDetailLoaded({
  api,
  tenantId,
  customer,
  onClose,
}: {
  api: OmniApiClient;
  tenantId: string;
  customer: Awaited<ReturnType<OmniApiClient["customer"]>>;
  onClose: () => void;
}) {
  const cache = useQueryClient();
  const [name, setName] = useState(customer.name);
  const [notes, setNotes] = useState(customer.notes ?? "");
  const [contactName, setContactName] = useState("");
  const [locationLabel, setLocationLabel] = useState("");
  const refresh = () =>
    cache.invalidateQueries({ queryKey: ["customer", tenantId, customer.id] });
  const update = useMutation({
    mutationFn: () =>
      api.updateCustomer(customer.id, {
        name: name.trim(),
        email: customer.email,
        phone: customer.phone,
        external_ref: customer.external_ref,
        notes: notes || null,
        expected_version: customer.version,
      }),
    onSuccess: refresh,
  });
  const addContact = useMutation({
    mutationFn: () =>
      api.addContact(customer.id, {
        name: contactName.trim(),
        email: null,
        phone: null,
        role: null,
        is_primary: !customer.contacts.length,
      }),
    onSuccess: async () => {
      setContactName("");
      await refresh();
    },
  });
  const addLocation = useMutation({
    mutationFn: () =>
      api.addLocation(customer.id, {
        label: locationLabel.trim(),
        address_line1: "Address to confirm",
        address_line2: null,
        city: "City to confirm",
        region: null,
        postal_code: null,
        country: "US",
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    onSuccess: async () => {
      setLocationLabel("");
      await refresh();
    },
  });
  return (
    <FlatList
      data={customer.activity}
      keyExtractor={(item) => item.id}
      contentContainerStyle={styles.list}
      ListHeaderComponent={
        <View style={styles.list}>
          <Pressable onPress={onClose}>
            <Text style={styles.link}>← All customers</Text>
          </Pressable>
          <View style={styles.form}>
            <TextInput
              accessibilityLabel="Customer name"
              style={styles.input}
              value={name}
              onChangeText={setName}
            />
            <TextInput
              accessibilityLabel="Customer notes"
              style={styles.input}
              value={notes}
              onChangeText={setNotes}
              multiline
            />
            <PrimaryButton
              label="Save customer"
              onPress={() => update.mutate()}
            />
          </View>
          <Text style={styles.subtitle}>Contacts</Text>
          {customer.contacts.map((item) => (
            <Record
              key={item.id}
              title={item.name}
              detail={item.email ?? item.role ?? "Contact"}
            />
          ))}
          <View style={styles.toolbar}>
            <TextInput
              accessibilityLabel="Contact name"
              placeholder="Contact name"
              style={[styles.input, { flex: 1 }]}
              value={contactName}
              onChangeText={setContactName}
            />
            <PrimaryButton
              disabled={!contactName.trim()}
              label="Add"
              onPress={() => addContact.mutate()}
            />
          </View>
          <Text style={styles.subtitle}>Locations</Text>
          {customer.locations.map((item) => (
            <Record
              key={item.id}
              title={item.label}
              detail={`${item.address_line1}, ${item.city}`}
            />
          ))}
          <View style={styles.toolbar}>
            <TextInput
              accessibilityLabel="Location label"
              placeholder="Location label"
              style={[styles.input, { flex: 1 }]}
              value={locationLabel}
              onChangeText={setLocationLabel}
            />
            <PrimaryButton
              disabled={!locationLabel.trim()}
              label="Add"
              onPress={() => addLocation.mutate()}
            />
          </View>
          <Text style={styles.subtitle}>Activity</Text>
        </View>
      }
      renderItem={({ item }) => (
        <Record
          title={item.action.replaceAll(".", " ")}
          detail={new Date(item.occurred_at).toLocaleString()}
        />
      )}
    />
  );
}

function MobileOperations({
  api,
  tenantId,
  section,
  customers,
}: {
  api: OmniApiClient;
  tenantId: string;
  section: "jobs" | "schedule" | "invoices" | "team";
  customers: Array<{ id: string; name: string }>;
}) {
  const cache = useQueryClient();
  const [title, setTitle] = useState("");
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [technicianName, setTechnicianName] = useState("");
  const [technicianEmail, setTechnicianEmail] = useState("");
  const jobs = useQuery({
    queryKey: ["jobs", tenantId],
    queryFn: () => api.jobs(),
  });
  const appointments = useQuery({
    queryKey: ["appointments", tenantId],
    queryFn: () => api.appointments(),
  });
  const invoices = useQuery({
    queryKey: ["invoices", tenantId],
    queryFn: () => api.invoices(),
  });
  const technicians = useQuery({
    queryKey: ["technicians", tenantId],
    queryFn: () => api.technicians(),
  });
  const refreshJobs = () =>
    cache.invalidateQueries({ queryKey: ["jobs", tenantId] });
  const create = useMutation({
    mutationFn: () =>
      api.createJob({
        customer_id: customers[0].id,
        title: title.trim(),
      } as JobCreate),
    onSuccess: async () => {
      setTitle("");
      await refreshJobs();
    },
  });
  const schedule = useMutation({
    mutationFn: (job: Job) => {
      const startsAt = new Date(Date.now() + 24 * 60 * 60 * 1000);
      const endsAt = new Date(startsAt.getTime() + 60 * 60 * 1000);
      return api.scheduleJob(job.id, {
        starts_at: startsAt.toISOString(),
        ends_at: endsAt.toISOString(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        assignee: null,
        expected_version: job.version,
      });
    },
    onSuccess: async () => {
      await Promise.all([
        refreshJobs(),
        cache.invalidateQueries({ queryKey: ["appointments", tenantId] }),
      ]);
    },
  });
  const complete = useMutation({
    mutationFn: (job: Job) => api.completeJob(job.id, job.version),
    onSuccess: refreshJobs,
  });
  const draft = useMutation({
    mutationFn: (job: Job) =>
      api.draftInvoice(job.id, {
        expected_job_version: job.version,
        currency: "USD",
        lines: [
          { description: "Service work", quantity: 1, unit_price_cents: 10000 },
        ],
      }),
    onSuccess: async () => {
      await Promise.all([
        refreshJobs(),
        cache.invalidateQueries({ queryKey: ["invoices", tenantId] }),
      ]);
    },
  });
  const issue = useMutation({
    mutationFn: (invoice: { id: string; version: number }) =>
      api.issueInvoice(invoice.id, invoice.version),
    onSuccess: async () => {
      await Promise.all([
        refreshJobs(),
        cache.invalidateQueries({ queryKey: ["invoices", tenantId] }),
      ]);
    },
  });
  const reschedule = useMutation({
    mutationFn: (appointment: Appointment) => {
      const startsAt = new Date(appointment.starts_at);
      const endsAt = new Date(appointment.ends_at);
      startsAt.setDate(startsAt.getDate() + 1);
      endsAt.setDate(endsAt.getDate() + 1);
      return api.rescheduleAppointment(appointment.id, {
        starts_at: startsAt.toISOString(),
        ends_at: endsAt.toISOString(),
        timezone: appointment.timezone,
        technician_id: appointment.technician_id,
        expected_version: appointment.version,
      });
    },
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: ["appointments", tenantId] }),
  });
  const payment = useMutation({
    mutationFn: (invoice: {
      id: string;
      total_cents: number;
      paid_cents: number;
    }) =>
      api.recordPayment(invoice.id, {
        amount_cents: invoice.total_cents - invoice.paid_cents,
        method: "card",
        external_ref: null,
        received_at: null,
      }),
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: ["invoices", tenantId] }),
  });
  const createTechnician = useMutation({
    mutationFn: () =>
      api.createTechnician({
        name: technicianName.trim(),
        email: technicianEmail.trim(),
        phone: null,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    onSuccess: async (technician) => {
      await Promise.all(
        [0, 1, 2, 3, 4].map((weekday) =>
          api.addTechnicianAvailability(technician.id, {
            weekday,
            start_minute: 9 * 60,
            end_minute: 17 * 60,
          }),
        ),
      );
      setTechnicianName("");
      setTechnicianEmail("");
      await cache.invalidateQueries({ queryKey: ["technicians", tenantId] });
    },
  });
  const voidInvoice = useMutation({
    mutationFn: (invoice: { id: string; version: number }) =>
      api.voidInvoice(invoice.id, invoice.version),
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: ["invoices", tenantId] }),
  });
  if (section === "schedule") {
    return (
      <FlatList
        data={appointments.data ?? []}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => (
          <View style={styles.customer}>
            <View style={{ flex: 1 }}>
              <Text style={styles.customerName}>
                {new Date(item.starts_at).toLocaleString()}
              </Text>
              <Text style={styles.body}>
                {item.assignee ?? "Unassigned"} · {item.status}
              </Text>
            </View>
            <PrimaryButton
              label="+1 day"
              onPress={() => reschedule.mutate(item)}
            />
          </View>
        )}
      />
    );
  }
  if (section === "team") {
    return (
      <View style={styles.list}>
        <View style={styles.form}>
          <TextInput
            accessibilityLabel="Technician name"
            placeholder="Technician name"
            style={styles.input}
            value={technicianName}
            onChangeText={setTechnicianName}
          />
          <TextInput
            accessibilityLabel="Technician email"
            placeholder="Email"
            autoCapitalize="none"
            keyboardType="email-address"
            style={styles.input}
            value={technicianEmail}
            onChangeText={setTechnicianEmail}
          />
          <PrimaryButton
            disabled={!technicianName.trim() || !technicianEmail.trim()}
            label="Add technician"
            onPress={() => createTechnician.mutate()}
          />
        </View>
        {(technicians.data ?? []).map((technician) => (
          <Record
            key={technician.id}
            title={technician.name}
            detail={`${technician.email} · ${technician.timezone}`}
          />
        ))}
      </View>
    );
  }
  if (section === "invoices") {
    const invoicedJobIds = new Set(
      invoices.data?.items.map((invoice) => invoice.job_id) ?? [],
    );
    const completed =
      jobs.data?.items.filter(
        (job) => job.status === "completed" && !invoicedJobIds.has(job.id),
      ) ?? [];
    return (
      <View style={styles.list}>
        {completed.map((job) => (
          <PrimaryButton
            key={job.id}
            label={`Draft invoice · ${job.title}`}
            onPress={() => draft.mutate(job)}
          />
        ))}
        {(invoices.data?.items ?? []).map((invoice) => (
          <View key={invoice.id} style={styles.customer}>
            <View style={{ flex: 1 }}>
              <Text style={styles.customerName}>{invoice.number}</Text>
              <Text style={styles.body}>
                ${(invoice.total_cents / 100).toFixed(2)} · {invoice.status}
              </Text>
            </View>
            {invoice.status === "draft" && (
              <PrimaryButton
                label="Issue"
                onPress={() => issue.mutate(invoice)}
              />
            )}
            {invoice.status === "issued" &&
              (invoice.payment_status === "paid" ? (
                <Text style={styles.link}>Paid</Text>
              ) : (
                <PrimaryButton
                  label="Mark paid"
                  onPress={() => payment.mutate(invoice)}
                />
              ))}
            {invoice.status === "issued" && invoice.paid_cents === 0 && (
              <Pressable onPress={() => voidInvoice.mutate(invoice)}>
                <Text style={styles.error}>Void</Text>
              </Pressable>
            )}
          </View>
        ))}
      </View>
    );
  }
  if (selectedJob) {
    return (
      <MobileJobDetail
        api={api}
        tenantId={tenantId}
        job={selectedJob}
        onClose={() => setSelectedJob(null)}
      />
    );
  }
  return (
    <View style={styles.list}>
      <View style={styles.form}>
        <TextInput
          accessibilityLabel="Job title"
          placeholder="New job title"
          style={styles.input}
          value={title}
          onChangeText={setTitle}
        />
        <PrimaryButton
          disabled={!title.trim() || !customers.length}
          label="Create job"
          onPress={() => create.mutate()}
        />
      </View>
      {(jobs.data?.items ?? []).map((job) => (
        <View key={job.id} style={styles.customer}>
          <View style={{ flex: 1 }}>
            <Text style={styles.customerName}>{job.title}</Text>
            <Text style={styles.body}>{job.status}</Text>
          </View>
          {job.status === "draft" && (
            <PrimaryButton
              label="Schedule tomorrow"
              onPress={() => schedule.mutate(job)}
            />
          )}
          {job.status === "scheduled" && (
            <PrimaryButton
              label="Complete"
              onPress={() => complete.mutate(job)}
            />
          )}
          <Pressable onPress={() => setSelectedJob(job)}>
            <Text style={styles.link}>Details</Text>
          </Pressable>
        </View>
      ))}
    </View>
  );
}

function MobileJobDetail({
  api,
  tenantId,
  job,
  onClose,
}: {
  api: OmniApiClient;
  tenantId: string;
  job: Job;
  onClose: () => void;
}) {
  const cache = useQueryClient();
  const [note, setNote] = useState("");
  const notes = useQuery({
    queryKey: ["job-notes", tenantId, job.id],
    queryFn: () => api.jobNotes(job.id),
  });
  const history = useQuery({
    queryKey: ["job-history", tenantId, job.id],
    queryFn: () => api.jobHistory(job.id),
  });
  const attachments = useQuery({
    queryKey: ["job-attachments", tenantId, job.id],
    queryFn: () => api.jobAttachments(job.id),
  });
  const addNote = useMutation({
    mutationFn: () => api.addJobNote(job.id, note.trim()),
    onSuccess: async () => {
      setNote("");
      await cache.invalidateQueries({
        queryKey: ["job-notes", tenantId, job.id],
      });
    },
  });
  return (
    <FlatList
      data={notes.data ?? []}
      keyExtractor={(item) => item.id}
      contentContainerStyle={styles.list}
      ListHeaderComponent={
        <View style={styles.list}>
          <Pressable onPress={onClose}>
            <Text style={styles.link}>← All jobs</Text>
          </Pressable>
          <Text style={styles.subtitle}>{job.title}</Text>
          <View style={styles.toolbar}>
            <TextInput
              accessibilityLabel="Job note"
              placeholder="Add note"
              style={[styles.input, { flex: 1 }]}
              value={note}
              onChangeText={setNote}
            />
            <PrimaryButton
              disabled={!note.trim()}
              label="Add"
              onPress={() => addNote.mutate()}
            />
          </View>
          <Text style={styles.subtitle}>Attachments</Text>
          {attachments.data?.map((item) => (
            <Record
              key={item.id}
              title={item.filename}
              detail={`${(item.size_bytes / 1024).toFixed(1)} KB`}
            />
          ))}
          <Text style={styles.subtitle}>Status history</Text>
          {history.data?.map((item) => (
            <Record
              key={item.id}
              title={item.to_status}
              detail={new Date(item.occurred_at).toLocaleString()}
            />
          ))}
          <Text style={styles.subtitle}>Notes</Text>
        </View>
      }
      renderItem={({ item }) => (
        <Record
          title={item.body}
          detail={new Date(item.created_at).toLocaleString()}
        />
      )}
    />
  );
}

function MobileApproval({
  approval,
  pending,
  onDecision,
}: {
  approval: Approval;
  pending: boolean;
  onDecision: (
    decision: "approve" | "reject" | "edit",
    argumentsValue?: Record<string, unknown>,
  ) => void;
}) {
  const [argumentsValue, setArgumentsValue] = useState<Record<string, unknown>>(
    { ...approval.proposed_args },
  );
  return (
    <View style={styles.approvalCard}>
      <Text style={styles.eyebrow}>HUMAN REVIEW REQUIRED</Text>
      <Text style={styles.subtitle}>Review proposed action</Text>
      {Object.entries(argumentsValue).map(([name, value]) => (
        <View key={name} style={styles.fieldGroup}>
          <Text style={styles.fieldLabel}>{name.replaceAll("_", " ")}</Text>
          <TextInput
            accessibilityLabel={`Approval ${name}`}
            value={value === null || value === undefined ? "" : String(value)}
            style={styles.input}
            onChangeText={(next) =>
              setArgumentsValue((current) => ({
                ...current,
                [name]: typeof value === "number" ? Number(next) : next || null,
              }))
            }
          />
        </View>
      ))}
      <PrimaryButton
        disabled={pending}
        label="Approve"
        onPress={() => onDecision("approve")}
      />
      <PrimaryButton
        disabled={pending}
        label="Save edits & approve"
        onPress={() => onDecision("edit", argumentsValue)}
      />
      <Pressable disabled={pending} onPress={() => onDecision("reject")}>
        <Text style={styles.error}>Reject without changes</Text>
      </Pressable>
    </View>
  );
}

function MobileChatMessage({ message }: { message: ConversationMessage }) {
  return (
    <View
      style={[
        styles.mobileMessage,
        message.role === "user" && styles.mobileMessageUser,
      ]}
    >
      <Text
        style={
          message.role === "user"
            ? styles.mobileMessageUserText
            : styles.eyebrow
        }
      >
        {message.role === "user" ? "YOU" : "OMNI AGENT"}
      </Text>
      <Text
        style={
          message.role === "user" ? styles.mobileMessageUserText : styles.body
        }
      >
        {message.content}
      </Text>
      {message.citations.map((citation, index) => {
        const source = citation as { id?: string; title?: string };
        return (
          <Text key={`${source.id}-${index}`} style={styles.link}>
            ↗ {source.title ?? "Source"}
          </Text>
        );
      })}
    </View>
  );
}

function MobileAutomations({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const templates = useQuery({
    queryKey: ["automation-templates", tenantId],
    queryFn: () => api.automationTemplates(),
  });
  const definitions = useQuery({
    queryKey: ["automations", tenantId],
    queryFn: () => api.automations(),
  });
  const runs = useQuery({
    queryKey: ["automation-runs", tenantId],
    queryFn: () => api.automationRuns(),
    refetchInterval: 10_000,
  });
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["automations", tenantId] }),
      cache.invalidateQueries({ queryKey: ["automation-runs", tenantId] }),
    ]);
  };
  const install = useMutation({
    mutationFn: (key: string) => api.installAutomationTemplate(key),
    onSuccess: refresh,
  });
  const activate = useMutation({
    mutationFn: ({ id, mode }: { id: string; mode: "shadow" | "production" }) =>
      api.activateAutomation(id, mode),
    onSuccess: refresh,
  });
  const pause = useMutation({
    mutationFn: (id: string) => api.pauseAutomation(id),
    onSuccess: refresh,
  });
  const review = useMutation({
    mutationFn: ({
      approvalId,
      decision,
    }: {
      approvalId: string;
      decision: "approve" | "reject";
    }) =>
      api.decideAutomationApproval(approvalId, {
        decision,
        actions: null,
        reason: `${decision}d on mobile`,
      }),
    onSuccess: refresh,
  });
  const installed = new Set(definitions.data?.map((item) => item.template_key));
  return (
    <ScrollView contentContainerStyle={styles.automationMobile}>
      <View>
        <Text style={styles.eyebrow}>SAFE ROLLOUT</Text>
        <Text style={styles.subtitle}>Automation templates</Text>
        <Text style={styles.body}>
          Install in shadow mode before promoting.
        </Text>
      </View>
      {templates.data?.map((template) => (
        <View key={template.key} style={styles.mobileToolCard}>
          <Text style={styles.customerName}>{template.name}</Text>
          <Text style={styles.body}>{template.description}</Text>
          <Text style={styles.fieldLabel}>{template.trigger_type}</Text>
          <PrimaryButton
            disabled={installed.has(template.key) || install.isPending}
            label={
              installed.has(template.key) ? "Installed" : "Install in shadow"
            }
            onPress={() => install.mutate(template.key)}
          />
        </View>
      ))}
      <Text style={styles.subtitle}>Active control</Text>
      {definitions.data?.map((definition) => (
        <View key={definition.id} style={styles.mobileToolCard}>
          <Text style={styles.customerName}>{definition.name}</Text>
          <Text style={styles.body}>
            {definition.status} · {definition.mode} · v
            {definition.current_version}
          </Text>
          <Text style={styles.fieldLabel}>
            WHEN {definition.version.trigger_type}
          </Text>
          <View style={styles.mobileRunActions}>
            {definition.status !== "active" &&
              definition.status !== "killed" && (
                <PrimaryButton
                  label="Start shadow"
                  onPress={() =>
                    activate.mutate({ id: definition.id, mode: "shadow" })
                  }
                />
              )}
            {definition.status === "active" && definition.mode === "shadow" && (
              <PrimaryButton
                label="Promote"
                onPress={() =>
                  activate.mutate({ id: definition.id, mode: "production" })
                }
              />
            )}
            {definition.status === "active" && (
              <Pressable onPress={() => pause.mutate(definition.id)}>
                <Text style={styles.link}>Pause</Text>
              </Pressable>
            )}
          </View>
        </View>
      ))}
      <Text style={styles.subtitle}>Recent runs</Text>
      {runs.data?.map((run: AutomationRun) => (
        <View key={run.id} style={styles.mobileToolCard}>
          <Text style={styles.customerName}>{run.definition_name}</Text>
          <Text style={styles.fieldLabel}>
            {run.status.toUpperCase()} · V{run.version}
          </Text>
          <Text style={styles.body}>{run.reason}</Text>
          <Text style={styles.body}>
            {run.changed_resources.length} record(s) changed
          </Text>
          {run.approval?.status === "pending" && (
            <View style={styles.approvalCard}>
              <Text style={styles.customerName}>Review required</Text>
              <PrimaryButton
                label="Approve"
                onPress={() =>
                  review.mutate({
                    approvalId: run.approval!.id,
                    decision: "approve",
                  })
                }
              />
              <Pressable
                onPress={() =>
                  review.mutate({
                    approvalId: run.approval!.id,
                    decision: "reject",
                  })
                }
              >
                <Text style={styles.error}>Reject</Text>
              </Pressable>
            </View>
          )}
        </View>
      ))}
    </ScrollView>
  );
}

function MobileAssistant({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [activeRun, setActiveRun] = useState<AgentRunDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const conversations = useQuery({
    queryKey: ["ai-conversations", tenantId],
    queryFn: () => api.conversations(),
  });
  const selectedConversationId =
    conversationId ?? conversations.data?.[0]?.id ?? null;
  const messages = useQuery({
    queryKey: ["ai-messages", tenantId, selectedConversationId],
    queryFn: () => api.conversationMessages(selectedConversationId!),
    enabled: Boolean(selectedConversationId),
  });
  const run = useMutation({
    mutationFn: async (content: string) => {
      let selected = selectedConversationId;
      if (!selected) {
        const conversation = await api.createConversation();
        selected = conversation.id;
        setConversationId(selected);
      }
      const detail = await api.createAgentRun(selected, content);
      setActiveRun(detail);
      setEvents([]);
      await api.streamAgentRun(detail.run.id, 0, (event) =>
        setEvents((current) => [...current, event]),
      );
      await cache.invalidateQueries({
        queryKey: ["ai-messages", tenantId, selected],
      });
      return detail;
    },
  });
  const review = useMutation({
    mutationFn: async ({
      decision,
      argumentsValue,
    }: {
      decision: "approve" | "reject" | "edit";
      argumentsValue?: Record<string, unknown>;
    }) => {
      const approval = activeRun!.approvals.find(
        (item) => item.status === "pending",
      )!;
      const detail = await api.decideApproval(approval.id, {
        decision,
        arguments: argumentsValue,
      });
      setActiveRun(detail);
      await cache.invalidateQueries({
        queryKey: ["ai-messages", tenantId, selectedConversationId],
      });
      return detail;
    },
  });
  const cancel = useMutation({
    mutationFn: async () => {
      const detail = await api.cancelAgentRun(activeRun!.run.id);
      setActiveRun(detail);
      return detail;
    },
  });
  const regenerate = useMutation({
    mutationFn: async () => {
      const detail = await api.regenerateAgentRun(activeRun!.run.id);
      setActiveRun(detail);
      await cache.invalidateQueries({
        queryKey: ["ai-messages", tenantId, selectedConversationId],
      });
      return detail;
    },
  });
  const feedback = useMutation({
    mutationFn: (rating: "up" | "down") =>
      api.addAgentFeedback(activeRun!.run.id, {
        rating,
        category: rating === "up" ? "helpful" : "incorrect",
      }),
  });
  const approval = activeRun?.approvals.find(
    (item) => item.status === "pending",
  );
  const streamText = events
    .filter((event) => event.event_type === "text_delta")
    .map((event) => String(event.payload.delta ?? ""))
    .join("");
  return (
    <View style={styles.assistantMobile}>
      <View style={styles.toolbar}>
        <Text style={styles.body}>Ask, review, then act.</Text>
        <PrimaryButton
          label="+ New"
          onPress={() => {
            setConversationId(null);
            setActiveRun(null);
            setEvents([]);
          }}
        />
      </View>
      <ScrollView contentContainerStyle={styles.messageList}>
        {!messages.data?.length && (
          <View style={styles.empty}>
            <Text style={styles.emptyIcon}>✦</Text>
            <Text style={styles.subtitle}>What should we work on?</Text>
            <Text style={styles.body}>
              Ask about a customer or policy, or propose an action for review.
            </Text>
          </View>
        )}
        {messages.data?.map((message) => (
          <MobileChatMessage key={message.id} message={message} />
        ))}
        {streamText && run.isPending && (
          <View style={styles.mobileMessage}>
            <Text style={styles.eyebrow}>OMNI AGENT</Text>
            <Text style={styles.body}>{streamText}</Text>
          </View>
        )}
        {activeRun?.tools.map((tool) => (
          <View key={tool.id} style={styles.mobileToolCard}>
            <Text style={styles.customerName}>
              ↗ {tool.name.replaceAll("_", " ")}
            </Text>
            <Text style={styles.body}>
              {tool.risk} · {tool.status.replaceAll("_", " ")}
            </Text>
            {Object.entries(tool.input).map(([name, value]) => (
              <Text key={name} style={styles.body}>
                {name.replaceAll("_", " ")}:{" "}
                {value == null ? "Not set" : String(value)}
              </Text>
            ))}
          </View>
        ))}
        {approval && (
          <MobileApproval
            approval={approval}
            pending={review.isPending}
            onDecision={(decision, argumentsValue) =>
              review.mutate({ decision, argumentsValue })
            }
          />
        )}
        {(run.error || review.error) && (
          <Text style={styles.error}>
            {(run.error ?? review.error)?.message}
          </Text>
        )}
      </ScrollView>
      {activeRun?.run.status === "completed" && (
        <View style={styles.mobileRunActions}>
          <Pressable onPress={() => feedback.mutate("up")}>
            <Text style={styles.link}>👍 Helpful</Text>
          </Pressable>
          <Pressable onPress={() => feedback.mutate("down")}>
            <Text style={styles.link}>👎 Incorrect</Text>
          </Pressable>
          <Pressable onPress={() => regenerate.mutate()}>
            <Text style={styles.link}>Regenerate</Text>
          </Pressable>
        </View>
      )}
      {activeRun?.run.status === "failed" && (
        <Pressable onPress={() => regenerate.mutate()}>
          <Text style={styles.link}>Retry failed run</Text>
        </Pressable>
      )}
      {activeRun &&
        ["running", "waiting_approval"].includes(activeRun.run.status) && (
          <Pressable onPress={() => cancel.mutate()}>
            <Text style={styles.error}>Stop run</Text>
          </Pressable>
        )}
      <View style={styles.mobileComposer}>
        <TextInput
          accessibilityLabel="Message Omni Agent"
          multiline
          placeholder="Ask Omni Agent…"
          style={[styles.input, { flex: 1 }]}
          value={prompt}
          onChangeText={setPrompt}
        />
        <PrimaryButton
          disabled={!prompt.trim() || run.isPending || Boolean(approval)}
          label="Send"
          onPress={() => {
            const content = prompt.trim();
            setPrompt("");
            run.mutate(content);
          }}
        />
      </View>
      <Text style={styles.disclaimer}>
        Consequential actions always require approval.
      </Text>
    </View>
  );
}

function Record({ title, detail }: { title: string; detail: string }) {
  return (
    <View style={styles.customer}>
      <View>
        <Text style={styles.customerName}>{title}</Text>
        <Text style={styles.body}>{detail}</Text>
      </View>
    </View>
  );
}

function PrimaryButton({
  label,
  onPress,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={[styles.button, disabled && styles.disabled]}
    >
      <Text style={styles.buttonText}>{label}</Text>
    </Pressable>
  );
}

function StateView({ label }: { label: string }) {
  return (
    <SafeAreaView style={styles.authPage}>
      <ActivityIndicator color={colors.primary} />
      <Text style={styles.body}>{label}</Text>
    </SafeAreaView>
  );
}

type AppStyles = {
  page: ViewStyle;
  authPage: ViewStyle;
  authCard: ViewStyle;
  eyebrow: TextStyle;
  hero: TextStyle;
  title: TextStyle;
  subtitle: TextStyle;
  body: TextStyle;
  header: ViewStyle;
  toolbar: ViewStyle;
  form: ViewStyle;
  input: TextStyle;
  button: ViewStyle;
  buttonText: TextStyle;
  disabled: ViewStyle;
  link: TextStyle;
  error: TextStyle;
  empty: ViewStyle;
  emptyIcon: TextStyle;
  list: ViewStyle;
  customer: ViewStyle;
  avatar: ViewStyle;
  avatarText: TextStyle;
  customerName: TextStyle;
  mobileNav: ViewStyle;
  activeNav: TextStyle;
  inactiveNav: TextStyle;
  tenantSwitcher: ViewStyle;
  tenantPill: ViewStyle;
  tenantPillActive: ViewStyle;
  tenantText: TextStyle;
  assistantMobile: ViewStyle;
  messageList: ViewStyle;
  mobileMessage: ViewStyle;
  mobileMessageUser: ViewStyle;
  mobileMessageUserText: TextStyle;
  mobileToolCard: ViewStyle;
  approvalCard: ViewStyle;
  fieldGroup: ViewStyle;
  fieldLabel: TextStyle;
  mobileRunActions: ViewStyle;
  mobileComposer: ViewStyle;
  disclaimer: TextStyle;
  automationMobile: ViewStyle;
  voiceMobile: ViewStyle;
  intelligenceMobile: ViewStyle;
};

const styles = StyleSheet.create<AppStyles>({
  page: { flex: 1, backgroundColor: colors.paper, padding: spacing.lg },
  authPage: {
    flex: 1,
    justifyContent: "center",
    padding: spacing.lg,
    backgroundColor: colors.paper,
    gap: spacing.md,
  },
  authCard: {
    backgroundColor: colors.surface,
    padding: spacing.xl,
    borderRadius: radii.lg,
    gap: spacing.md,
  },
  eyebrow: {
    color: colors.primary,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.4,
  },
  hero: { color: colors.ink, fontSize: 34, fontWeight: "700", lineHeight: 39 },
  title: { color: colors.ink, fontSize: 30, fontWeight: "700" },
  subtitle: { color: colors.ink, fontSize: 18, fontWeight: "700" },
  body: { color: colors.muted, fontSize: 14 },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.lg,
  },
  toolbar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.lg,
  },
  form: {
    backgroundColor: colors.surface,
    padding: spacing.md,
    gap: spacing.md,
    borderRadius: radii.md,
    marginBottom: spacing.lg,
  },
  input: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.sm,
    padding: 13,
    backgroundColor: colors.surface,
    color: colors.ink,
  },
  button: {
    backgroundColor: colors.primary,
    paddingHorizontal: 17,
    paddingVertical: 12,
    borderRadius: radii.sm,
    alignItems: "center",
  },
  buttonText: { color: "white", fontWeight: "700" },
  disabled: { opacity: 0.5 },
  link: { color: colors.primary, fontWeight: "700" },
  error: { color: colors.danger },
  empty: { alignItems: "center", paddingVertical: 60, gap: spacing.sm },
  emptyIcon: { color: colors.primary, fontSize: 38 },
  list: { gap: spacing.sm },
  customer: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
  },
  avatar: {
    width: 44,
    height: 44,
    borderRadius: radii.sm,
    backgroundColor: colors.primarySoft,
    justifyContent: "center",
    alignItems: "center",
  },
  avatarText: { color: colors.primary, fontWeight: "700" },
  customerName: { color: colors.ink, fontWeight: "700", fontSize: 16 },
  mobileNav: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.md,
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    paddingBottom: spacing.md,
    marginBottom: spacing.lg,
  },
  activeNav: { color: colors.primary, fontWeight: "700" },
  inactiveNav: { color: colors.muted },
  tenantSwitcher: {
    flexDirection: "row",
    gap: spacing.sm,
    marginBottom: spacing.md,
  },
  tenantPill: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  tenantPillActive: { backgroundColor: colors.primarySoft },
  tenantText: { color: colors.ink, fontWeight: "600" },
  assistantMobile: { flex: 1, gap: spacing.sm },
  messageList: {
    gap: spacing.md,
    paddingBottom: spacing.xl,
  },
  mobileMessage: {
    alignSelf: "flex-start",
    maxWidth: "88%",
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
  },
  mobileMessageUser: {
    alignSelf: "flex-end",
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  mobileMessageUserText: { color: "white", fontSize: 14 },
  mobileToolCard: {
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radii.md,
    borderLeftColor: colors.primary,
    borderLeftWidth: 4,
    backgroundColor: colors.surface,
  },
  approvalCard: {
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radii.md,
    borderColor: "#e3bd69",
    borderWidth: 1,
    backgroundColor: "#fffaf0",
  },
  fieldGroup: { gap: spacing.sm },
  fieldLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    textTransform: "capitalize",
  },
  mobileRunActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    gap: spacing.sm,
    paddingVertical: spacing.sm,
  },
  mobileComposer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: spacing.sm,
  },
  disclaimer: { color: colors.muted, fontSize: 11, textAlign: "center" },
  automationMobile: { gap: spacing.md, paddingBottom: spacing.xl },
  voiceMobile: { gap: spacing.md, paddingBottom: spacing.xl },
  intelligenceMobile: { gap: spacing.md, paddingBottom: spacing.xl },
});
