import {
  CustomerCreate,
  Job,
  JobCreate,
  OmniApiClient,
  Tenant,
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
  const [section, setSection] = useState<
    "customers" | "jobs" | "schedule" | "invoices"
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
        {(["customers", "jobs", "schedule", "invoices"] as const).map(
          (item) => (
            <Pressable key={item} onPress={() => setSection(item)}>
              <Text
                style={section === item ? styles.activeNav : styles.inactiveNav}
              >
                {item[0].toUpperCase() + item.slice(1)}
              </Text>
            </Pressable>
          ),
        )}
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
      {section === "customers" && (
        <FlatList
          data={customers.data?.items ?? []}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.list}
          renderItem={({ item }) => (
            <View style={styles.customer}>
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
            </View>
          )}
        />
      )}
      {section !== "customers" && (
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

function MobileOperations({
  api,
  tenantId,
  section,
  customers,
}: {
  api: OmniApiClient;
  tenantId: string;
  section: "jobs" | "schedule" | "invoices";
  customers: Array<{ id: string; name: string }>;
}) {
  const cache = useQueryClient();
  const [title, setTitle] = useState("");
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
          <Record
            title={new Date(item.starts_at).toLocaleString()}
            detail={`${item.assignee ?? "Unassigned"} · ${item.status}`}
          />
        )}
      />
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
            {invoice.status === "issued" && (
              <PrimaryButton
                label="Void"
                onPress={() => voidInvoice.mutate(invoice)}
              />
            )}
          </View>
        ))}
      </View>
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
        </View>
      ))}
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
});
