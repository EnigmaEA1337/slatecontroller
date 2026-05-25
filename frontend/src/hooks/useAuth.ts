import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  getMe as apiGetMe,
  login as apiLogin,
  logout as apiLogout,
} from "@/api/auth";
import { clearToken, getToken, setToken } from "@/lib/auth-storage";
import type { User } from "@/types/auth";

export function useCurrentUser() {
  return useQuery<User>({
    queryKey: ["me"],
    queryFn: apiGetMe,
    enabled: Boolean(getToken()),
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      apiLogin(username, password),
    onSuccess: (data) => {
      setToken(data.access_token);
      queryClient.invalidateQueries({ queryKey: ["me"] });
      navigate("/");
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: apiLogout,
    onSettled: () => {
      clearToken();
      queryClient.clear();
      navigate("/login");
    },
  });
}
